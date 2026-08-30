from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

RUNNING = "running"
DONE = "done"
FAILED = "failed"
STATUSES = frozenset({RUNNING, DONE, FAILED})


def named() -> str:
    """A name for one review, unique enough to put in a link."""
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ReviewRecord:
    """One review, kept so it can be looked at again.

    Nothing about keeping one is local. It is held rather than answered on the
    spot because the thing that asks for a review is often not the thing that
    reads it.

    An agent runs one over MCP and hands somebody a link, and the link has to
    still work when they open it.
    """

    id: str
    repository: str
    status: str = RUNNING
    # What the reading found. Empty while it is still running, and left empty
    # when it failed.
    answer: Mapping[str, Any] = field(default_factory=dict)
    # Why it failed, in the words of whatever failed.
    error: str = ""
    title: str = ""
    started: datetime = field(default_factory=now)
    finished: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.repository:
            raise ValueError("a review needs a name and a repository")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(sorted(STATUSES))}")

    @property
    def ready(self) -> bool | None:
        """Whether the work is ready, or None while nobody knows yet."""
        if self.status != DONE:
            return None
        return bool(self.answer.get("ready", True))
