from __future__ import annotations

from collections import defaultdict, deque

from src.core.scope import Scope

from .models import (
    TopologyEntity,
    TopologyRelationship,
    TopologySubgraph,
    TopologyTraversal,
)


class InMemoryTopologyRepository:
    def __init__(self) -> None:
        self._entities: dict[tuple[Scope, str], TopologyEntity] = {}
        self._relationships: dict[tuple[Scope, str], TopologyRelationship] = {}
        self._adjacency: dict[tuple[Scope, str], set[str]] = defaultdict(set)

    def put_entity(self, scope: Scope, entity: TopologyEntity) -> None:
        self._entities[(scope, entity.id)] = entity

    def put_relationship(
        self, scope: Scope, relationship: TopologyRelationship
    ) -> None:
        if (scope, relationship.source_id) not in self._entities or (
            scope,
            relationship.target_id,
        ) not in self._entities:
            raise ValueError("relationship endpoints must exist in the same scope")
        key = scope, relationship.id
        previous = self._relationships.get(key)
        if previous:
            self._adjacency[(scope, previous.source_id)].discard(previous.id)
            self._adjacency[(scope, previous.target_id)].discard(previous.id)
        self._relationships[key] = relationship
        self._adjacency[(scope, relationship.source_id)].add(relationship.id)
        self._adjacency[(scope, relationship.target_id)].add(relationship.id)

    def traverse(self, traversal: TopologyTraversal) -> TopologySubgraph:
        scope = traversal.scope
        queue = deque(
            (entity, 0)
            for entity_id in traversal.entity_ids
            if (entity := self._entities.get((scope, entity_id)))
            and self._matches_entity(entity, traversal)
        )
        queued = {entity.id for entity, _ in queue}
        visited: set[str] = set()
        entities: list[TopologyEntity] = []
        relationships: dict[str, TopologyRelationship] = {}
        truncated = False

        while queue:
            entity, distance = queue.popleft()
            if entity.id in visited:
                continue
            if len(entities) >= traversal.entity_limit:
                truncated = True
                continue
            visited.add(entity.id)
            entities.append(entity)
            if distance == traversal.depth:
                continue

            for relationship_id in sorted(self._adjacency.get((scope, entity.id), ())):
                relationship = self._relationships[(scope, relationship_id)]
                if not self._matches_relationship(relationship, entity.id, traversal):
                    continue
                other_id = (
                    relationship.target_id
                    if relationship.source_id == entity.id
                    else relationship.source_id
                )
                target = self._entities.get((scope, other_id))
                if not target or not self._matches_entity(target, traversal):
                    continue
                if (
                    relationship.id not in relationships
                    and len(relationships) >= traversal.relationship_limit
                ):
                    truncated = True
                    continue
                relationships[relationship.id] = relationship
                if target.id not in queued:
                    queued.add(target.id)
                    queue.append((target, distance + 1))

        packed_relationships = tuple(
            relationship
            for relationship in relationships.values()
            if relationship.source_id in visited and relationship.target_id in visited
        )
        return TopologySubgraph(
            entities=tuple(entities),
            relationships=packed_relationships,
            truncated=truncated or len(packed_relationships) != len(relationships),
        )

    @staticmethod
    def _matches_entity(entity: TopologyEntity, traversal: TopologyTraversal) -> bool:
        return (
            (not traversal.entity_kinds or entity.kind in traversal.entity_kinds)
            and (
                not traversal.entity_statuses
                or entity.status in traversal.entity_statuses
            )
            and entity.confidence >= traversal.minimum_confidence
            and (traversal.include_stale or not entity.stale)
        )

    @staticmethod
    def _matches_relationship(
        relationship: TopologyRelationship,
        entity_id: str,
        traversal: TopologyTraversal,
    ) -> bool:
        return (
            (
                not traversal.relationship_types
                or relationship.type in traversal.relationship_types
            )
            and (
                not traversal.relationship_statuses
                or relationship.status in traversal.relationship_statuses
            )
            and relationship.confidence >= traversal.minimum_confidence
            and (traversal.include_stale or not relationship.stale)
            and (
                traversal.direction == "both"
                or (
                    traversal.direction == "outbound"
                    and relationship.source_id == entity_id
                )
                or (
                    traversal.direction == "inbound"
                    and relationship.target_id == entity_id
                )
            )
        )
