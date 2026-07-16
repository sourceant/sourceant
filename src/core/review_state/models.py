from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    state: str
    summary: str
    code_anchor: str | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FindingQuery:
    scope: Scope
    states: frozenset[str] = field(default_factory=frozenset)
    properties: Mapping[str, Any] = field(default_factory=dict)
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if self.offset < 0:
            raise ValueError("offset must not be negative")


@dataclass(frozen=True)
class FindingResult:
    findings: tuple[ReviewFinding, ...]
    total: int
    has_more: bool
