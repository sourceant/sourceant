import base64
import json
import os
import tempfile
from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from src.api.routes.topology import get_scope
from src.core.responses import success_response
from src.core.scope import Scope
from src.core.services import service_registry
from src.core.storage import ArtifactKey, ArtifactStore, ArtifactWrite

router = APIRouter()
MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.presentation",
        "application/rtf",
        "text/rtf",
        "text/csv",
        "text/tab-separated-values",
        "application/json",
        "application/xml",
        "text/xml",
        "application/yaml",
        "application/x-yaml",
        "text/yaml",
        "text/x-yaml",
        "application/zip",
        "application/gzip",
        "application/x-tar",
        "application/x-7z-compressed",
        "image/gif",
        "image/bmp",
        "image/tiff",
        "image/svg+xml",
        "image/png",
        "image/jpeg",
        "image/webp",
        "text/plain",
        "text/markdown",
    }
)


def upload_limit() -> int:
    return max(1, int(os.environ.get("ARTIFACT_MAX_UPLOAD_BYTES", "2000000")))


def get_artifacts():
    try:
        return service_registry.resolve(ArtifactStore)
    except LookupError as error:
        raise HTTPException(503, "Artifact store is unavailable") from error


def component(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or len(value) > 255
        or any(c in value for c in "/\\\0")
    ):
        raise HTTPException(422, "Invalid artifact identifier")
    return value


def artifact_key(scope, namespace, name, version):
    if len(namespace) > 64:
        raise HTTPException(
            422, "Artifact namespace must contain at most 64 characters"
        )
    return ArtifactKey(scope, component(namespace), component(name), component(version))


def reference(key: ArtifactKey) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                [key.namespace, key.name, key.version], separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


def key_from_reference(scope: Scope, value: str) -> ArtifactKey:
    try:
        parts = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if (
            not isinstance(parts, list)
            or len(parts) != 3
            or not all(isinstance(part, str) for part in parts)
        ):
            raise ValueError("Invalid artifact reference")
        return artifact_key(scope, *parts)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, "Invalid artifact reference") from error


def payload(artifact):
    return jsonable_encoder({**asdict(artifact), "reference": reference(artifact.key)})


@router.post("/{namespace}/{name}")
async def upload(
    namespace: str,
    name: str,
    request: Request,
    scope: Scope = Depends(get_scope),
    store=Depends(get_artifacts),
):
    media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type not in MEDIA_TYPES:
        raise HTTPException(415, "Unsupported artifact media type")
    limit = upload_limit()
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
        except ValueError as error:
            raise HTTPException(400, "Invalid content length") from error
        if length > limit:
            raise HTTPException(413, "Artifact exceeds upload limit")
    key = artifact_key(scope, namespace, name, uuid4().hex)
    with tempfile.SpooledTemporaryFile(max_size=min(limit, 65536)) as content:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "Artifact exceeds upload limit")
            content.write(chunk)
        content.seek(0)
        from starlette.concurrency import run_in_threadpool

        artifact = await run_in_threadpool(
            store.put, ArtifactWrite(key, media_type), content
        )
    return success_response(payload(artifact))


@router.get("/{namespace}/{name}/{version}")
def download(
    namespace: str,
    name: str,
    version: str,
    scope: Scope = Depends(get_scope),
    store=Depends(get_artifacts),
):
    key = artifact_key(scope, namespace, name, version)
    artifact = store.get(key)
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    content = store.open(key)

    def chunks():
        try:
            while chunk := content.read(65536):
                yield chunk
        finally:
            content.close()

    return StreamingResponse(
        chunks(),
        media_type=artifact.media_type,
        headers={
            "Content-Length": str(artifact.size),
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{namespace}")
def list_artifacts(
    namespace: str,
    name: str | None = None,
    scope: Scope = Depends(get_scope),
    store=Depends(get_artifacts),
):
    return success_response(
        [
            payload(item)
            for item in store.list(
                scope, component(namespace), component(name) if name else None
            )
        ]
    )


@router.delete("/{namespace}/{name}/{version}")
def remove(
    namespace: str,
    name: str,
    version: str,
    scope: Scope = Depends(get_scope),
    store=Depends(get_artifacts),
):
    if not store.delete(artifact_key(scope, namespace, name, version)):
        raise HTTPException(404, "Artifact not found")
    return success_response({"deleted": True})
