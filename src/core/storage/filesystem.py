from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from src.core.scope import Scope

from .models import (
    Artifact,
    ArtifactKey,
    ArtifactWrite,
    ContentDigest,
    WorkingArea,
    WorkingAreaRequest,
)


def _component(value: str) -> str:
    if value in {".", ".."} or not value or any(char in value for char in "/\\\0"):
        raise ValueError("storage identifiers must be single path components")
    return value


def _scope_id(scope: Scope) -> str:
    encoded = json.dumps(scope.values, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class FileSystemArtifactStore:
    def __init__(self, root: Path):
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def get(self, key: ArtifactKey) -> Artifact | None:
        metadata = self._metadata_path(key)
        if not metadata.is_file():
            return None
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        return Artifact(
            key=key,
            digest=ContentDigest(payload["digest_algorithm"], payload["digest"]),
            size=payload["size"],
            media_type=payload["media_type"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            properties=payload["properties"],
        )

    def open(self, key: ArtifactKey) -> BinaryIO:
        return self._content_path(key).open("rb")

    def list(
        self, scope: Scope, namespace: str, name: str | None = None
    ) -> tuple[Artifact, ...]:
        base = self._root / _scope_id(scope) / _component(namespace)
        if name is not None:
            base /= _component(name)
        if not base.exists():
            return ()
        artifacts = []
        for metadata in sorted(base.rglob("metadata.json")):
            relative = metadata.relative_to(self._root / _scope_id(scope))
            parts = relative.parts
            key = ArtifactKey(scope, parts[0], parts[1], parts[2])
            artifact = self.get(key)
            if artifact is not None:
                artifacts.append(artifact)
        return tuple(artifacts)

    def put(self, request: ArtifactWrite, content: BinaryIO) -> Artifact:
        target = self._content_path(request.key)
        if self._metadata_path(request.key).exists():
            raise FileExistsError(f"artifact already exists: {request.key.version}")
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.new(request.digest_algorithm)
        size = 0
        temporary = tempfile.NamedTemporaryFile(dir=target.parent, delete=False)
        temporary_path = Path(temporary.name)
        try:
            with temporary:
                while chunk := content.read(1024 * 1024):
                    temporary.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            value = digest.hexdigest()
            actual = ContentDigest(request.digest_algorithm, value)
            if (
                request.expected_digest is not None
                and request.expected_digest != actual
            ):
                raise ValueError("artifact content does not match expected digest")
            os.replace(temporary_path, target)
            artifact = Artifact(
                key=request.key,
                digest=actual,
                size=size,
                media_type=request.media_type,
                created_at=datetime.now(timezone.utc),
                properties=dict(request.properties),
            )
            self._write_metadata(artifact)
            return artifact
        finally:
            temporary_path.unlink(missing_ok=True)

    def delete(self, key: ArtifactKey) -> bool:
        directory = self._directory(key)
        if not directory.exists():
            return False
        shutil.rmtree(directory)
        return True

    def _directory(self, key: ArtifactKey) -> Path:
        return (
            self._root
            / _scope_id(key.scope)
            / _component(key.namespace)
            / _component(key.name)
            / _component(key.version)
        )

    def _content_path(self, key: ArtifactKey) -> Path:
        return self._directory(key) / "content"

    def _metadata_path(self, key: ArtifactKey) -> Path:
        return self._directory(key) / "metadata.json"

    def _write_metadata(self, artifact: Artifact) -> None:
        payload = {
            "created_at": artifact.created_at.isoformat(),
            "digest": artifact.digest.value,
            "digest_algorithm": artifact.digest.algorithm,
            "media_type": artifact.media_type,
            "properties": dict(artifact.properties),
            "size": artifact.size,
        }
        path = self._metadata_path(artifact.key)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)


class FileSystemWorkingAreaStore:
    def __init__(self, root: Path):
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def provision(self, request: WorkingAreaRequest) -> WorkingArea:
        area = self._area(request)
        area.path.mkdir(parents=True, exist_ok=True)
        return area

    def get(self, request: WorkingAreaRequest) -> WorkingArea | None:
        area = self._area(request)
        return area if area.path.is_dir() else None

    def remove(self, request: WorkingAreaRequest) -> bool:
        area = self.get(request)
        if area is None:
            return False
        shutil.rmtree(area.path)
        return True

    def list(self, scope: Scope, purpose: str | None = None) -> tuple[WorkingArea, ...]:
        scope_root = self._root / _scope_id(scope)
        purposes = (
            [scope_root / _component(purpose)] if purpose else scope_root.glob("*")
        )
        areas = []
        for purpose_path in purposes:
            if not purpose_path.is_dir():
                continue
            for path in sorted(purpose_path.iterdir()):
                if path.is_dir():
                    request = WorkingAreaRequest(scope, purpose_path.name, path.name)
                    areas.append(WorkingArea(request, path))
        return tuple(areas)

    def _area(self, request: WorkingAreaRequest) -> WorkingArea:
        path = (
            self._root
            / _scope_id(request.scope)
            / _component(request.purpose)
            / _component(request.name)
        )
        return WorkingArea(request, path)
