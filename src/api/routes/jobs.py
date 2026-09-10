"""What background work has been asked for, and what became of it.

A job is answered only to the workspace it belongs to. Jobs are numbered in one
sequence across every customer on an instance, so an id alone must not be
enough to read one.
"""

from typing import Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query

from sqlmodel import Session, select

from src.auth import get_current_user
from src.config.db import get_engine
from src.core.jobs import LANES, Batch, Job, job_store
from src.core.responses import success_response
from src.core.workspace import workspace_of
from src.models.repository import Repository
from src.models.repository_event import RepositoryEvent

router = APIRouter()


def _moment(value) -> Optional[str]:
    return value.isoformat() if value else None


def _as_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "lane": job.lane,
        "kind": job.kind,
        "state": job.state,
        "tenant": job.tenant,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "batch_id": job.batch_id,
        "error": job.error,
        "created_at": _moment(job.created_at),
        "started_at": _moment(job.started_at),
        "finished_at": _moment(job.finished_at),
    }


def _subjects(jobs: Sequence[Job]) -> dict:
    """What each job is about, in the words a reader recognises.

    A job payload carries ids, so a feed built from it alone reads as a list of
    kinds and times with no subject. The names are resolved here, in two
    queries rather than one per row.
    """
    events = {
        job.payload.get("repository_event_id")
        for job in jobs
        if job.payload.get("repository_event_id")
    }
    repositories = {
        job.payload.get("repository_id")
        for job in jobs
        if job.payload.get("repository_id")
    }
    engine = get_engine()
    if engine is None or not (events or repositories):
        return {}
    found = {}
    with Session(engine) as session:
        if events:
            for row in session.exec(
                select(RepositoryEvent).where(RepositoryEvent.id.in_(events))
            ).all():
                found[("event", row.id)] = {
                    "repo": row.repository_full_name,
                    "number": row.number,
                    "title": row.title,
                }
        if repositories:
            for row in session.exec(
                select(Repository).where(Repository.id.in_(repositories))
            ).all():
                found[("repository", row.id)] = {
                    "repo": row.full_name,
                    "number": None,
                    "title": None,
                }
    return found


def _about(job: Job, subjects: dict) -> dict:
    key = (
        ("event", job.payload.get("repository_event_id"))
        if job.payload.get("repository_event_id")
        else ("repository", job.payload.get("repository_id"))
    )
    return subjects.get(key, {"repo": None, "number": None, "title": None})


def _theirs(owner: str, tenant: str) -> bool:
    """Whether work attributed to `tenant` belongs to whoever is asking.

    Work nobody could attribute belongs to nobody, rather than to everybody.
    """
    return tenant == owner


@router.get("")
async def list_jobs(
    lane: Optional[str] = Query(default=None),
    kind: list[str] = Query(default=[]),
    waiting_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """What background work has been asked for, newest first.

    Defaults to every state rather than the queue alone. A reader looking at
    this wants to see the review that just ran, and on a keeping-up instance
    nothing is ever waiting long enough to be seen.
    """
    if lane and lane not in LANES:
        raise HTTPException(status_code=400, detail=f"No lane called {lane!r}")
    owner = workspace_of(user)
    # Read wider than asked for: the filter is by tenant, and a page of rows
    # belonging to somebody else would otherwise come back short.
    store = job_store()
    found = (
        store.pending(lane=lane or "", limit=limit * 4)
        if waiting_only
        else store.recent(lane=lane or "", kinds=tuple(kind), limit=limit * 4)
    )
    wanted = frozenset(kind)
    mine = [
        job
        for job in found
        if _theirs(owner, job.tenant) and (not wanted or job.kind in wanted)
    ][:limit]
    subjects = _subjects(mine)
    return success_response(
        [{**_as_dict(job), **_about(job, subjects)} for job in mine]
    )


@router.get("/{job_id}")
async def read_job(job_id: int, user: dict = Depends(get_current_user)):
    job = job_store().read(job_id)
    if job is None or not _theirs(workspace_of(user), job.tenant):
        raise HTTPException(status_code=404, detail="No such job")
    return success_response(_as_dict(job))


@router.get("/batches/{batch_id}")
async def read_batch(batch_id: int, user: dict = Depends(get_current_user)):
    batch: Optional[Batch] = job_store().read_batch(batch_id)
    if batch is None or not _theirs(workspace_of(user), batch.tenant):
        raise HTTPException(status_code=404, detail="No such batch")
    return success_response(
        {
            "id": batch.id,
            "name": batch.name,
            "total": batch.total,
            "done": batch.done,
            "pending": batch.pending,
            "failed": batch.failed,
            "finished": batch.finished,
        }
    )
