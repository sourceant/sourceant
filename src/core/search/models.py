from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.scope import Scope


@dataclass(frozen=True)
class CodeTextQuery:
    scope: Scope
    terms: tuple[str, ...]
    limit: int = 8

    def __post_init__(self):
        if not self.scope.get("repository") or not self.scope.get("revision"):
            raise ValueError("Code text search requires a repository and revision")
        if not 1 <= len(self.terms) <= 16 or any(
            not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9]{2,63}", term) for term in self.terms
        ):
            raise ValueError("Supply between one and sixteen code search terms")
        if not 1 <= self.limit <= 20:
            raise ValueError("Code text search limit must be between one and twenty")


@dataclass(frozen=True)
class CodeTextMatch:
    path: str
    revision: str
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True)
class CodeTextResult:
    matches: tuple[CodeTextMatch, ...] = ()
    truncated: bool = False
    unavailable: str | None = None
