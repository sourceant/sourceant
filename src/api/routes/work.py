"""Work across the workspace's repositories: list, change, comment, track."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.auth import get_current_user
from src.core.responses import success_response
from src.core.work import (
    Kind,
    Priority,
    Status,
    Tracker,
    TrackerError,
    WorkChange,
    WorkDraft,
    WorkQuery,
)
from src.core.work.github import FINDING_LABEL, GitHubTracker
from src.utils.pagination import Params, page_of

router = APIRouter()


def tracker_for(user: dict) -> Tracker:
    """The tracker this workspace keeps its work in."""
    token = user.get("github_token")
    if not token:
        raise HTTPException(400, "Sign in with GitHub to read and change work")
    return GitHubTracker(token)


def _allowed(user: dict, repos: list[str]) -> list[str]:
    from src.api.routes.requirements import connected_names

    connected = set(connected_names(user))
    outside = [repo for repo in repos if repo not in connected]
    if outside:
        raise HTTPException(403, f"Outside this workspace: {', '.join(outside)}")
    return repos


class ItemRef(BaseModel):
    repo: str
    number: int


class ChangeRequest(WorkChange):
    repo: str
    number: int


class BulkRequest(BaseModel):
    items: list[ItemRef] = Field(min_length=1, max_length=100)
    change: WorkChange


class CommentRequest(ItemRef):
    body: str = Field(min_length=1)


class TrackRequest(BaseModel):
    repo: str
    title: str = Field(min_length=1)
    detail: str = ""
    path: str | None = None
    line: int | None = None
    revision: str | None = None
    pull: int | None = None
    priority: Priority | None = None
    assignees: list[str] = Field(default_factory=list)


@router.get("")
async def list_work(
    repo: list[str] = Query(..., description="Repository full name; may repeat."),
    status: list[Status] = Query(default=[]),
    priority: list[Priority] = Query(default=[]),
    kind: list[Kind] = Query(default=[]),
    assignee: str | None = None,
    text: str | None = None,
    include_done: bool = False,
    params: Params = Depends(),
    user: dict = Depends(get_current_user),
):
    """One page of work across the given repositories, newest change first."""
    tracker = tracker_for(user)
    query = WorkQuery(
        status=status,
        priority=priority,
        kind=kind,
        assignee=assignee,
        text=text,
        include_done=include_done,
    )
    try:
        found = await tracker.items(_allowed(user, repo), query)
    except TrackerError as error:
        raise HTTPException(502, str(error))
    return success_response(page_of([item.model_dump() for item in found], params))


@router.get("/people")
async def people(repo: str = Query(...), user: dict = Depends(get_current_user)):
    """Who work in a repository can be assigned to."""
    tracker = tracker_for(user)
    try:
        found = await tracker.people(_allowed(user, [repo])[0])
    except TrackerError as error:
        raise HTTPException(502, str(error))
    return success_response([person.model_dump() for person in found])


@router.patch("/item")
async def change_item(data: ChangeRequest, user: dict = Depends(get_current_user)):
    tracker = tracker_for(user)
    repo = _allowed(user, [data.repo])[0]
    change = WorkChange(**data.model_dump(exclude={"repo", "number"}))
    try:
        item = await tracker.update(repo, data.number, change)
    except TrackerError as error:
        raise HTTPException(502, str(error))
    return success_response(item.model_dump())


@router.post("/bulk")
async def change_many(data: BulkRequest, user: dict = Depends(get_current_user)):
    """Apply one change to many items; each is reported, so one refusal hides nothing."""
    tracker = tracker_for(user)
    _allowed(user, list(dict.fromkeys(ref.repo for ref in data.items)))
    changed, failed = [], []
    for ref in data.items:
        try:
            changed.append(
                (await tracker.update(ref.repo, ref.number, data.change)).model_dump()
            )
        except TrackerError as error:
            failed.append({**ref.model_dump(), "error": str(error)})
    return success_response({"changed": changed, "failed": failed})


@router.post("/comment")
async def comment(data: CommentRequest, user: dict = Depends(get_current_user)):
    tracker = tracker_for(user)
    try:
        await tracker.comment(_allowed(user, [data.repo])[0], data.number, data.body)
    except TrackerError as error:
        raise HTTPException(502, str(error))
    return success_response({"ok": True})


@router.post("/track")
async def track(data: TrackRequest, user: dict = Depends(get_current_user)):
    """Keep a review finding as work, linked back to the code it is about."""
    tracker = tracker_for(user)
    repo = _allowed(user, [data.repo])[0]
    where = []
    if data.path:
        location = f"{data.path}:{data.line}" if data.line else data.path
        if data.revision:
            anchor = f"#L{data.line}" if data.line else ""
            link = f"https://github.com/{repo}/blob/{data.revision}/{data.path}{anchor}"
            where.append(f"[`{location}`]({link})")
        else:
            where.append(f"`{location}`")
    if data.pull:
        where.append(f"#{data.pull}")
    body = "\n\n".join(
        part
        for part in (
            data.detail.strip(),
            f"Found in {' on '.join(where)}." if where else "",
            "Tracked from a SourceAnt review.",
        )
        if part
    )
    draft = WorkDraft(
        title=data.title,
        body=body,
        priority=data.priority,
        labels=[FINDING_LABEL],
        assignees=data.assignees,
    )
    try:
        item = await tracker.create(repo, draft)
    except TrackerError as error:
        raise HTTPException(502, str(error))
    return success_response(item.model_dump())
