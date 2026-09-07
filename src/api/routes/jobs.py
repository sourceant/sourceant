"""What background work has been asked for, and what became of it.

A job is answered only to the workspace it belongs to. Jobs are numbered in one
sequence across every customer on an instance, so an id alone must not be
enough to read one.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.auth import get_current_user
from src.core.jobs import LANES, Batch, Job, job_store
from src.core.responses import success_response
from src.core.workspace import workspace_of

router = APIRouter()


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
    }


def _theirs(owner: str, tenant: str) -> bool:
    """Whether work attributed to `tenant` belongs to whoever is asking.

    Work nobody could attribute belongs to nobody, rather than to everybody.
    """
    return tenant == owner


@router.get("")
async def list_jobs(
    lane: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    if lane and lane not in LANES:
        raise HTTPException(status_code=400, detail=f"No lane called {lane!r}")
    owner = workspace_of(user)
    waiting = job_store().pending(lane=lane or "", limit=limit)
    return success_response(
        [_as_dict(job) for job in waiting if _theirs(owner, job.tenant)]
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
