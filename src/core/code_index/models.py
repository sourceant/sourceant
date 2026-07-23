from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope


@dataclass(frozen=True)
class CodeNode:
    id: str
    labels: frozenset[str] = field(default_factory=frozenset)
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeEdge:
    id: str
    source_id: str
    target_id: str
    type: str
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeTraversal:
    scope: Scope
    node_ids: tuple[str, ...]
    depth: int = 2
    edge_types: frozenset[str] = field(default_factory=frozenset)
    direction: str = "both"
    node_limit: int = 50

    def __post_init__(self) -> None:
        if not self.node_ids or len(self.node_ids) > 100:
            raise ValueError("node_ids must contain between 1 and 100 values")
        if len(self.node_ids) != len(set(self.node_ids)):
            raise ValueError("node_ids must be unique")
        if not 1 <= self.depth <= 5:
            raise ValueError("depth must be between 1 and 5")
        if not 1 <= self.node_limit <= 100:
            raise ValueError("node_limit must be between 1 and 100")
        if self.direction not in {"outbound", "inbound", "both"}:
            raise ValueError("direction must be outbound, inbound, or both")


@dataclass(frozen=True)
class CodeSearch:
    scope: Scope
    labels: frozenset[str] = field(default_factory=frozenset)
    properties: Mapping[str, Any] = field(default_factory=dict)
    limit: int = 50
    offset: int = 0
    node_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if len(self.node_ids) > 100 or any(not node_id for node_id in self.node_ids):
            raise ValueError("node_ids must contain at most 100 non-empty values")
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.offset < 0:
            raise ValueError("offset must not be negative")


@dataclass(frozen=True)
class CodeTraversalResult:
    nodes: tuple[CodeNode, ...]
    edges: tuple[CodeEdge, ...]
    truncated: bool


@dataclass(frozen=True)
class CodeSearchResult:
    nodes: tuple[CodeNode, ...]
    total: int
    has_more: bool
