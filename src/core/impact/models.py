from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping

from src.core.scope import Scope
from src.core.topology import TopologyEvidence, TopologySubgraph


@dataclass(frozen=True)
class ChangedCodeReference:
    id: str
    kind: str
    revision: str
    path: str = ""
    #: Which repository the change is in. Two repositories in one system can
    #: hold the same path, so without this a seed written for one of them
    #: answers a review of the other.
    repository: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.kind or not self.revision:
            raise ValueError("changed code identity, kind, and revision are required")

    @property
    def identity(self) -> tuple[str, str, str]:
        """What makes two references the same change.

        A path, not a commit. What a file belongs to changes when the file
        moves, not when somebody opens a pull request, and keying on the
        revision meant a mapping written while reading a repository could
        never match the lookup a later review made: different commit,
        different key, no starting point, and a review that reached nothing.

        The kind is folded to lower case because the two sides spell it
        differently: a reading says "File" and a review says "file".
        """
        return (
            (self.kind or "").lower(),
            self.repository or "",
            self.path or self.id,
        )

    @property
    def key(self) -> str:
        """The identity as one short string, for a store that needs one."""
        return sha256("\0".join(self.identity).encode()).hexdigest()


@dataclass(frozen=True)
class CompatibilityCheck:
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
class CompatibilityCheckQuery:
    scope: Scope
    entity_ids: frozenset[str]
    statuses: frozenset[str] = field(default_factory=frozenset)
    minimum_confidence: float = 0.0
    include_stale: bool = False
    limit: int = 50

    def __post_init__(self) -> None:
        if not self.entity_ids or any(not item for item in self.entity_ids):
            raise ValueError("entity_ids must contain non-empty values")
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if self.limit < 1:
            raise ValueError("limit must be positive")


@dataclass(frozen=True)
class ChangeImpactRequest:
    scope: Scope
    changes: tuple[ChangedCodeReference, ...]
    depth: int = 2
    entity_limit: int = 50
    relationship_limit: int = 100
    #: How sure a compatibility finding must be before a review is told about
    #: it. Certain, because a finding is an assertion that something breaks.
    minimum_confidence: float = 1.0
    #: How sure the graph must be before the walk crosses it, which is a
    #: different question. Topology is derived by reading a repository and is
    #: never certain, so holding it to the same floor reaches nothing at all.
    minimum_reach_confidence: float = 0.0
    #: Which links the walk may cross. Every automatically discovered link
    #: is written pending and stays pending until a person approves it, so a
    #: system connected last week has no approved links at all. Both are
    #: crossed; what separates them is how the review is told.
    reach_statuses: frozenset[str] = frozenset({"approved", "pending"})

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
        if not 0.0 <= self.minimum_reach_confidence <= 1.0:
            raise ValueError("minimum_reach_confidence must be between 0 and 1")


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
class ChangeImpact:
    topology: TopologySubgraph
    compatibility: tuple[CompatibilityCheck, ...]
    findings: tuple[ImpactFinding, ...]
    truncated: bool
    #: Whether the walk had anywhere to start. Nothing recorded where the
    #: changed files sit, and a walk that never started reaches nothing, which
    #: is the same empty answer a walk that crossed the whole graph and found
    #: nothing gives. Reported apart, because only one of them is a fact about
    #: the change.
    seeded: bool = True
