from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope
from src.core.topology import TopologyEvidence, TopologySubgraph


@dataclass(frozen=True)
class ChangedCodeReference:
    id: str
    kind: str
    revision: str
    path: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.kind or not self.revision:
            raise ValueError("changed code identity, kind, and revision are required")


@dataclass(frozen=True)
class CompatibilityEvidence:
    id: str
    provider_entity_id: str
    consumer_entity_id: str
    status: str
    compatible: bool | None
    before_revision: str
    after_revision: str
    summary: str
    confidence: float = 1.0
    stale: bool = False
    evidence: tuple[TopologyEvidence, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("compatibility evidence id cannot be empty")
        if not self.provider_entity_id or not self.consumer_entity_id:
            raise ValueError("compatibility evidence must identify both endpoints")
        if not self.before_revision or not self.after_revision:
            raise ValueError("compatibility evidence must identify compared revisions")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class ReviewImpactRequest:
    scope: Scope
    changes: tuple[ChangedCodeReference, ...]
    depth: int = 2
    entity_limit: int = 50
    relationship_limit: int = 100
    minimum_confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.changes or len(self.changes) > 100:
            raise ValueError("changes must contain between 1 and 100 values")
        change_ids = [change.id for change in self.changes]
        if len(change_ids) != len(set(change_ids)):
            raise ValueError("changed code identities must be unique")
        if not 1 <= self.depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 1 <= self.entity_limit <= 50:
            raise ValueError("entity_limit must be between 1 and 50")
        if not 1 <= self.relationship_limit <= 500:
            raise ValueError("relationship_limit must be between 1 and 500")
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")


@dataclass(frozen=True)
class ImpactFinding:
    id: str
    state: str
    summary: str
    changed_code_ids: tuple[str, ...]
    topology_entity_ids: tuple[str, ...]
    compatibility_evidence_id: str
    certain: bool
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.state or not self.summary:
            raise ValueError("impact finding identity, state, and summary are required")
        if not self.changed_code_ids or any(not item for item in self.changed_code_ids):
            raise ValueError("impact finding must identify changed code")
        if not self.topology_entity_ids or any(
            not item for item in self.topology_entity_ids
        ):
            raise ValueError("impact finding must identify topology entities")
        if not self.compatibility_evidence_id:
            raise ValueError("impact finding must identify compatibility evidence")


@dataclass(frozen=True)
class ReviewImpact:
    topology: TopologySubgraph
    compatibility: tuple[CompatibilityEvidence, ...]
    findings: tuple[ImpactFinding, ...]
    truncated: bool
