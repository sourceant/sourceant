from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope

CODE = "code"
TEST = "test"
KNOWLEDGE = "knowledge"
TOPOLOGY = "topology"
ARTIFACT = "artifact"
TARGET_TYPES = frozenset({CODE, TEST, KNOWLEDGE, TOPOLOGY, ARTIFACT})


@dataclass(frozen=True)
class Requirement:
    id: str
    type: str
    status: str
    summary: str
    external_ref: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    priority: str = ""

    def __post_init__(self) -> None:
        from .scenarios import scenario_properties

        object.__setattr__(self, "properties", scenario_properties(self.properties))
        priority = self.priority or str(self.properties.get("priority") or "")
        if len(priority) > 255:
            raise ValueError("priority must contain at most 255 characters")
        object.__setattr__(self, "priority", priority)
        if priority:
            object.__setattr__(
                self, "properties", {**self.properties, "priority": priority}
            )
        if not self.id:
            raise ValueError("requirement id must not be empty")
        if not self.type:
            raise ValueError("requirement type must not be empty")
        if not self.status:
            raise ValueError("requirement status must not be empty")


@dataclass(frozen=True)
class RequirementLink:
    id: str
    requirement_id: str
    target_type: str
    target_id: str
    properties: Mapping[str, Any] = field(default_factory=dict)
    relation: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.requirement_id or not self.target_id:
            raise ValueError("a link needs an id, a requirement, and a target")
        inferred = {
            CODE: "implemented_by",
            TEST: "has_test_case",
            TOPOLOGY: "allocated_to",
            ARTIFACT: "described_by",
        }
        relation = (
            self.relation
            or self.properties.get("relation")
            or inferred.get(self.target_type, "relates_to")
        )
        if (
            not isinstance(relation, str)
            or not isinstance(self.target_type, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.target_type)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", relation)
        ):
            raise ValueError(
                "Target type and relationship use lowercase names with underscores or hyphens"
            )
        object.__setattr__(self, "relation", relation)
        object.__setattr__(
            self, "properties", {**self.properties, "relation": relation}
        )


@dataclass(frozen=True)
class RequirementQuery:
    scope: Scope
    ids: frozenset[str] = field(default_factory=frozenset)
    types: frozenset[str] = field(default_factory=frozenset)
    statuses: frozenset[str] = field(default_factory=frozenset)
    external_refs: frozenset[str] = field(default_factory=frozenset)
    limit: int = 50
    offset: int = 0
    priorities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.offset < 0:
            raise ValueError("offset must not be negative")
        for name, values in (
            ("ids", self.ids),
            ("types", self.types),
            ("statuses", self.statuses),
            ("external_refs", self.external_refs),
            ("priorities", self.priorities),
        ):
            if len(values) > 100:
                raise ValueError(f"{name} must contain at most 100 values")


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
    limit: int = 100

    def __post_init__(self) -> None:
        if any(not path for path in self.paths):
            raise ValueError("paths must not contain empty values")
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")


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
    truncated: bool = False

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
