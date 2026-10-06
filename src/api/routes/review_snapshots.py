from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from src.auth import get_current_user
from src.core.responses import success_response
from src.core.review.snapshot import SnapshotConfiguration, review_snapshot
from src.core.settings.configuration import Configuration
from src.core.settings.definitions import get
from src.core.workspace import workspace_of
from src.models.code_review import Verdict
from src.plugins.builtin.code_reviewer.working_tree import reviewed

router = APIRouter()
MAX_UPLOAD = 64 * 1024 * 1024
OPTIONS = {
    "discovery-passes": "review.discovery_passes",
    "evaluation-passes": "review.evaluation_passes",
    "concurrency": "review.concurrency",
    "minimum-support": "review.minimum_support",
    "maximum-rejections": "review.maximum_rejections",
    "reading-budget": "review.reading_budget",
    "max-output-tokens": "model.max_output_tokens",
    "reasoning-effort": "model.reasoning_effort",
    "include-nitpicks": "review.include_nitpicks",
    "finding-scope": "review.finding_scope",
    "reuse-responses-days": "review.reuse_responses_days",
}


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base: str = Field(pattern=r"^[a-f0-9]{40}$")
    head: str = Field(pattern=r"^[a-f0-9]{40}$")
    diff: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    files: dict[str, str] = Field(max_length=50000)
    omitted: list[str] = Field(default_factory=list, max_length=50000)
    title: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=100000)
    configuration: dict[str, str] = Field(default_factory=dict, max_length=16)

    @field_validator("files")
    @classmethod
    def valid_files(cls, files):
        for path, source in files.items():
            relative = PurePosixPath(path)
            if (
                not path
                or relative.is_absolute()
                or ".." in relative.parts
                or ".git" in relative.parts
                or relative.as_posix() != path
                or "\\" in path
            ):
                raise ValueError("Invalid snapshot path")
            if len(source.encode("utf-8")) > 1_000_000 or "\0" in source:
                raise ValueError("Unsupported snapshot content")
        return files


def configuration_for(body: Snapshot, user):
    base = Configuration(
        repository=body.repository, workspace=workspace_of(user), user=user["user_id"]
    )
    overrides = {"review.plan": "premium"}
    for label, value in body.configuration.items():
        if label in OPTIONS:
            overrides[OPTIONS[label]] = get(OPTIONS[label]).validate(value)
        elif label not in {"model", "review-models", "evaluation-models"}:
            raise ValueError("Unsupported review configuration")
    profiles = base.value("model.profiles") or {}
    purposes = dict(base.value("model.purposes") or {})
    for label, purpose in (
        ("model", "review"),
        ("review-models", "review"),
        ("evaluation-models", "review-evaluation"),
    ):
        if label not in body.configuration:
            continue
        aliases = [one.strip() for one in body.configuration[label].split(",")]
        if not aliases or any(one not in profiles for one in aliases):
            raise ValueError(
                "The requested model profile is not configured in this workspace"
            )
        purposes[purpose] = aliases
        if label == "model":
            purposes["review-evaluation"] = aliases
    if purposes:
        overrides["model.purposes"] = purposes
    return SnapshotConfiguration(
        repository=base.repository,
        workspace=base.workspace,
        user=base.user,
        overrides=overrides,
    )


@router.post("")
async def submit_snapshot(request: Request, user: dict = Depends(get_current_user)):
    workspace_of(user)
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_UPLOAD:
            raise HTTPException(413, "Review snapshot exceeds 64 MiB")
    try:
        body = Snapshot.model_validate_json(bytes(payload))
        configuration = configuration_for(body, user)
    except ValueError:
        raise HTTPException(422, "Invalid review snapshot or configuration") from None
    try:
        review = await run_in_threadpool(
            review_snapshot,
            repository=body.repository,
            base=body.base,
            head=body.head,
            diff=body.diff,
            files=body.files,
            title=body.title,
            description=body.description,
            configuration=configuration,
        )
    except ValueError:
        raise HTTPException(
            422, "The snapshot cannot be reviewed with the configured models"
        ) from None
    except Exception:
        raise HTTPException(502, "The snapshot review did not complete") from None
    answer = reviewed(review)
    applied = {label: str(configuration.value(key)) for label, key in OPTIONS.items()}
    applied.update(
        {
            key: value
            for key, value in body.configuration.items()
            if key in {"model", "review-models", "evaluation-models"}
        }
    )
    return success_response(
        {
            "status": "done",
            "configuration": applied,
            "review": {
                "base": body.base,
                "ready": review.verdict == Verdict.APPROVE and not body.omitted,
                "review": answer,
                "where": {"base": body.base, "branch": body.head},
            },
            "snapshot": {
                "repository": body.repository,
                "base": body.base,
                "head": body.head,
                "omitted": body.omitted,
            },
        }
    )
