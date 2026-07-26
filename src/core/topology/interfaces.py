from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    TopologyEntity,
    TopologyQuery,
    TopologyRelationship,
    TopologyResult,
    TopologySubgraph,
    TopologyTraversal,
)


@runtime_checkable
class TopologyReader(Protocol):
    def search(self, query: TopologyQuery) -> TopologyResult: ...

    def get_relationships(
        self,
        scope: Scope,
        entity_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[TopologyRelationship, ...]: ...

    def traverse(self, traversal: TopologyTraversal) -> TopologySubgraph: ...


@runtime_checkable
class TopologyWriter(Protocol):
    def put_entity(self, scope: Scope, entity: TopologyEntity) -> None: ...

    def put_relationship(
        self, scope: Scope, relationship: TopologyRelationship
    ) -> None: ...

    def remove_entity(self, scope: Scope, entity_id: str) -> bool:
        """Remove an entity and every relationship attached to it."""
        ...

    def remove_relationship(self, scope: Scope, relationship_id: str) -> bool: ...


@runtime_checkable
class TopologyRepository(TopologyReader, TopologyWriter, Protocol):
    pass
