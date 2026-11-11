from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any
from posixpath import commonpath, dirname

from .execution import ReviewTarget


def group_for(files):
    return commonpath([dirname(file["path"]) or "." for file in files]) if files else ""


@dataclass(frozen=True)
class ReviewChunk:
    id: str
    review_id: str
    files: tuple[ReviewTarget, ...] = ()
    group: str = ""
    status: str = "pending"
    attempts: int = 0
    result: Mapping[str, Any] | None = None

    def __post_init__(self):
        if not self.id or not self.review_id:
            raise ValueError("A review chunk needs an identity and review")
        if self.status not in {"pending", "running", "done", "failed"}:
            raise ValueError("Invalid review chunk status")
        if self.attempts < 0:
            raise ValueError("Review chunk attempts cannot be negative")
