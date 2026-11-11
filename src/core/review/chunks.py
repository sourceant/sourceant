from dataclasses import dataclass
from functools import cached_property
from hashlib import sha256
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
    diff: str = ""
    input_tokens: int = 0

    def __post_init__(self):
        if not self.id or not self.review_id:
            raise ValueError("A review chunk needs an identity and review")
        if self.status not in {"pending", "running", "done", "failed"}:
            raise ValueError("Invalid review chunk status")
        if self.diff and sha256(self.diff.encode()).hexdigest() != self.id:
            raise ValueError("Review chunk identity must match its diff")
        if self.attempts < 0:
            raise ValueError("Review chunk attempts cannot be negative")

    @classmethod
    def from_files(cls, files, *, review_id, input_tokens):
        diff = "\n".join(file.diff_text for file in files)
        targets = tuple(
            ReviewTarget(file.file_path, tuple(file.hunk_ranges)) for file in files
        )
        return cls(
            id=sha256(diff.encode()).hexdigest(),
            review_id=review_id,
            files=targets,
            group=group_for([{"path": target.path} for target in targets]),
            diff=diff,
            input_tokens=input_tokens,
        )

    @cached_property
    def parsed_files(self):
        from src.utils.diff_parser import parse_diff

        return tuple(parse_diff(self.diff))

    def definition(self):
        return {
            "id": self.id,
            "input_tokens": self.input_tokens,
            "targets": [
                {"path": file.path, "ranges": file.ranges} for file in self.files
            ],
        }
