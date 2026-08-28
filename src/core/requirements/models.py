from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope

CODE = "code"
TEST = "test"
KNOWLEDGE = "knowledge"
TOPOLOGY = "topology"
TARGET_KINDS = frozenset({CODE, TEST, KNOWLEDGE, TOPOLOGY})


@dataclass(frozen=True)
class Requirement:
    id: str
    kind: str
    status: str
    summary: str
    external_ref: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("requirement id must not be empty")
        if not self.kind:
            raise ValueError("requirement kind must not be empty")
        if not self.status:
            raise ValueError("requirement status must not be empty")


@dataclass(frozen=True)
class RequirementLink:
    id: str
    requirement_id: str
    target_kind: str
    target_id: str
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.requirement_id or not self.target_id:
            raise ValueError("a link needs an id, a requirement, and a target")
        if self.target_kind not in TARGET_KINDS:
            raise ValueError(
                f"target_kind must be one of {', '.join(sorted(TARGET_KINDS))}"
            )


@dataclass(frozen=True)
class RequirementQuery:
    scope: Scope
    ids: frozenset[str] = field(default_factory=frozenset)
    kinds: frozenset[str] = field(default_factory=frozenset)
    statuses: frozenset[str] = field(default_factory=frozenset)
    external_refs: frozenset[str] = field(default_factory=frozenset)
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.offset < 0:
            raise ValueError("offset must not be negative")


@dataclass(frozen=True)
class RequirementResult:
    items: tuple[Requirement, ...]
    total: int
    has_more: bool


@dataclass(frozen=True)
class CoverageQuery:
    scope: Scope
    requirement_ids: frozenset[str] = field(default_factory=frozenset)
    paths: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if any(not path for path in self.paths):
            raise ValueError("paths must not contain empty values")


@dataclass(frozen=True)
class RequirementSelection:
    scope: Scope
    paths: tuple[str, ...] = ()
    title: str = ""
    description: str = ""
    diff: str = ""
    limit: int = 20

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if any(not path for path in self.paths):
            raise ValueError("paths must not contain empty values")


@dataclass(frozen=True)
class RequirementCoverage:
    requirement_id: str
    status: str
    code_links: int
    test_links: int
    paths: tuple[str, ...]

    @property
    def covered(self) -> bool:
        return self.code_links > 0

    @property
    def tested(self) -> bool:
        return self.test_links > 0


@dataclass(frozen=True)
class CoverageReport:
    items: tuple[RequirementCoverage, ...]

    @property
    def uncovered(self) -> tuple[str, ...]:
        return tuple(item.requirement_id for item in self.items if not item.covered)

    @property
    def untested(self) -> tuple[str, ...]:
        return tuple(
            item.requirement_id
            for item in self.items
            if item.covered and not item.tested
        )
