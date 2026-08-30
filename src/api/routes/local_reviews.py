"""Asking for a review of a checkout, and reading one back.

Asking answers with an id immediately and reads behind it, so an agent can
hand over a link. The answer is kept so that link still opens it later.

The reviewing itself is in the code_reviewer plugin.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.routes.code import find_repository, require_local
from src.config.db import get_engine
from src.core.review import (
    DONE,
    FAILED,
    RUNNING,
    ReviewRecord,
    SQLReviewStore,
    named,
    now,
    working_tree_reviewer,
)
from src.core.responses import success_response

router = APIRouter()

_kept: Any = None


def get_reviews() -> Any:
    """Where reviews are kept.

    Creates the schema rather than waiting for a migration: this table is
    local, and starting the agent should not require running one.
    """
    global _kept
    if _kept is None:
        engine = get_engine()
        if engine is None:
            raise HTTPException(
                status_code=503, detail="This machine has nowhere to keep a review"
            )
        _kept = SQLReviewStore(engine, create_schema=True)
    return _kept


def get_working_tree_reviewer() -> Any:
    """Whatever registered as a working tree reviewer."""
    found = working_tree_reviewer()
    if found is None:
        raise HTTPException(
            status_code=503,
            detail="No working tree reviewer is registered.",
        )
    return found


class ReviewInput(BaseModel):
    repository: str = Field(...)
    # Empty means the checkout's remote head, which beats assuming "main".
    against: str = Field(default="")
    title: str = Field(default="")
    description: str = Field(default="")
    # Empty lets the change decide which skills apply.
    skills: list[str] = Field(default_factory=list)
    use_model: bool = Field(default=True)


# Longer than any review takes. Past this, the process reading it is gone.
ABANDONED = timedelta(minutes=30)


def kept(review: ReviewRecord) -> dict[str, Any]:
    """One review, in the shape a screen and an agent both read.

    A review still running past ABANDONED lost the process doing it, and is
    reported failed rather than left spinning.
    """
    status, error = review.status, review.error
    if status == RUNNING and now() - review.started > ABANDONED:
        status = FAILED
        error = (
            "Whatever was reading this stopped before it finished. Nothing was "
            "changed; ask for it again."
        )
    return {
        "id": review.id,
        "repository": review.repository,
        "status": status,
        "title": review.title,
        "error": error,
        "started": review.started.isoformat() if review.started else None,
        "finished": review.finished.isoformat() if review.finished else None,
        "review": dict(review.answer),
        "path": f"/reviews/{review.id}",
    }


def run(identifier: str, body: ReviewInput, judge: Any, reviews: Any) -> None:
    """Do the reading, and keep whatever came of it.

    The request that asked has already been answered, so a failure is recorded
    rather than raised.
    """
    try:
        answer = judge.review(
            repository=body.repository,
            against=body.against,
            title=body.title,
            description=body.description,
            skills=body.skills,
            use_model=body.use_model,
        )
    except Exception as error:  # noqa: BLE001 - what went wrong is the answer
        reviews.put(
            ReviewRecord(
                id=identifier,
                repository=body.repository,
                status=FAILED,
                error=getattr(error, "detail", None) or str(error),
                title=body.title,
                finished=now(),
            )
        )
        return

    reviews.put(
        ReviewRecord(
            id=identifier,
            repository=body.repository,
            status=DONE,
            answer=answer,
            title=body.title,
            finished=now(),
        )
    )


@router.post("", dependencies=[Depends(require_local)])
def start_review(
    body: ReviewInput,
    background: BackgroundTasks,
    judge: Any = Depends(get_working_tree_reviewer),
    reviews: Any = Depends(get_reviews),
):
    """Ask for a review, and get back where to find it."""
    # Checked here, so an unknown repository is refused rather than recorded
    # as a failed review.
    find_repository(body.repository)

    identifier = named()
    started = reviews.put(
        ReviewRecord(id=identifier, repository=body.repository, title=body.title)
    )
    background.add_task(run, identifier, body, judge, reviews)
    return success_response(kept(started))


@router.get("", dependencies=[Depends(require_local)])
def read_reviews(
    repository: str = "",
    limit: int = 20,
    reviews: Any = Depends(get_reviews),
):
    """The last few, newest first."""
    found = reviews.recent(repository=repository, limit=max(1, min(limit, 100)))
    return success_response([{**kept(review), "review": {}} for review in found])


@router.get("/{identifier}", dependencies=[Depends(require_local)])
def read_review(identifier: str, reviews: Any = Depends(get_reviews)):
    """One review, whether it is still running or long finished."""
    found = reviews.get(identifier)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail="No review by that name. It may have been one of the oldest.",
        )
    return success_response(kept(found))
