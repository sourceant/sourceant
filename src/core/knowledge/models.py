from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope


@dataclass(frozen=True)
class Knowledge:
    id: str
    kind: str
    status: str
    summary: str
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeRelationship:
    id: str
    source_id: str
    target_id: str
    type: str
    status: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeQuery:
    scope: Scope
    ids: frozenset[str] = field(default_factory=frozenset)
    kinds: frozenset[str] = field(default_factory=frozenset)
    statuses: frozenset[str] = field(default_factory=frozenset)
    properties: Mapping[str, Any] = field(default_factory=dict)
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.offset < 0:
            raise ValueError("offset must not be negative")


@dataclass(frozen=True)
class KnowledgeResult:
    items: tuple[Knowledge, ...]
    total: int
    has_more: bool


@dataclass(frozen=True)
class KnowledgeTraversal:
    scope: Scope
    knowledge_ids: tuple[str, ...]
    depth: int = 2
    relationship_types: frozenset[str] = field(default_factory=frozenset)
    relationship_statuses: frozenset[str] = field(default_factory=frozenset)
    direction: str = "both"
    knowledge_limit: int = 50
    relationship_limit: int = 100

    def __post_init__(self) -> None:
        if not self.knowledge_ids or len(self.knowledge_ids) > 50:
            raise ValueError("knowledge_ids must contain between 1 and 50 values")
        if len(self.knowledge_ids) != len(set(self.knowledge_ids)):
            raise ValueError("knowledge_ids must be unique")
        if not 1 <= self.depth <= 3:
            raise ValueError("depth must be between 1 and 3")
        if not 1 <= self.knowledge_limit <= 50:
            raise ValueError("knowledge_limit must be between 1 and 50")
        if not 1 <= self.relationship_limit <= 500:
            raise ValueError("relationship_limit must be between 1 and 500")
        if self.direction not in {"outbound", "inbound", "both"}:
            raise ValueError("direction must be outbound, inbound, or both")


@dataclass(frozen=True)
class KnowledgeSubgraph:
    items: tuple[Knowledge, ...]
    relationships: tuple[KnowledgeRelationship, ...]
    truncated: bool
