from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from src.core.scope import Scope


@dataclass(frozen=True)
class TopologyEvidence:
    id: str
    kind: str
    source: str
    revision: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("evidence id cannot be empty")


@dataclass(frozen=True)
class TopologyEntity:
    id: str
    kind: str
    status: str
    confidence: float = 1.0
    stale: bool = False
    properties: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[TopologyEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("entity id cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class TopologyRelationship:
    id: str
    source_id: str
    target_id: str
    type: str
    status: str
    confidence: float = 1.0
    stale: bool = False
    properties: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[TopologyEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("relationship id cannot be empty")
        if not self.source_id:
            raise ValueError("relationship source_id cannot be empty")
        if not self.target_id:
            raise ValueError("relationship target_id cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class TopologyTraversal:
    scope: Scope
    entity_ids: tuple[str, ...]
    depth: int = 2
    entity_kinds: frozenset[str] = field(default_factory=frozenset)
    entity_statuses: frozenset[str] = field(default_factory=frozenset)
    relationship_types: frozenset[str] = field(default_factory=frozenset)
    relationship_statuses: frozenset[str] = field(default_factory=frozenset)
    direction: Literal["outbound", "inbound", "both"] = "both"
    minimum_confidence: float = 0.0
    include_stale: bool = False
    entity_limit: int = 50
    relationship_limit: int = 100

    def __post_init__(self) -> None:
        if not self.entity_ids or len(self.entity_ids) > 50:
            raise ValueError("entity_ids must contain between 1 and 50 values")
        if len(self.entity_ids) != len(set(self.entity_ids)):
            raise ValueError("entity_ids must be unique")
        if any(not entity_id for entity_id in self.entity_ids):
            raise ValueError("entity_ids cannot contain empty values")
        if not 1 <= self.depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if not 1 <= self.entity_limit <= 50:
            raise ValueError("entity_limit must be between 1 and 50")
        if not 1 <= self.relationship_limit <= 500:
            raise ValueError("relationship_limit must be between 1 and 500")
        if self.direction not in {"outbound", "inbound", "both"}:
            raise ValueError("direction must be outbound, inbound, or both")


@dataclass(frozen=True)
class TopologySubgraph:
    entities: tuple[TopologyEntity, ...]
    relationships: tuple[TopologyRelationship, ...]
    truncated: bool
