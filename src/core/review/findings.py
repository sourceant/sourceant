from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope

# What core writes. The field is not closed to these: a deployment with its own
# workflow states files them here, and reading is by whichever states are asked
# for.
OPEN = "open"
FIXED = "fixed"
DISMISSED = "dismissed"


@dataclass(frozen=True)
class ReviewFinding:
    """One thing a review said, and what became of it.

    Kept apart from the review that raised it because it outlives it: the same
    problem raised twice is one finding seen twice.
    """

    id: str
    state: str
    summary: str
    # Where it was last seen. Never the identity: a line moves, a finding
    # does not.
    code_anchor: str | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.state:
            raise ValueError("a finding needs an id and a state")


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
