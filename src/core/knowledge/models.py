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
