from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from functools import wraps
from threading import RLock
from .batch import validate_hierarchy

from src.core.scope import Scope

from .models import (
    TopologyEntity,
    TopologyQuery,
    TopologyRelationship,
    TopologyResult,
    TopologySubgraph,
    TopologyTraversal,
)


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._batch_lock:
            return method(self, *args, **kwargs)

    return call


class InMemoryTopologyRepository:
    def __init__(self) -> None:
        self._batch_lock = RLock()
        self._operations = {}
        self._entities: dict[tuple[Scope, str], TopologyEntity] = {}
        self._relationships: dict[tuple[Scope, str], TopologyRelationship] = {}
        self._adjacency: dict[tuple[Scope, str], set[str]] = defaultdict(set)

    def apply_batch(self, scope, batch):
        with self._batch_lock:
            previous = self._operations.get((scope, batch.operation_id))
            if previous is not None:
                if previous != batch.digest:
                    raise ValueError(
                        "Operation ID was already used for another request"
                    )
                return
            staged = InMemoryTopologyRepository()
            staged._entities = deepcopy(self._entities)
            staged._relationships = deepcopy(self._relationships)
            staged._adjacency = deepcopy(self._adjacency)
            for identifier in batch.remove_relationships:
                staged.remove_relationship(scope, identifier)
            for entity in batch.entities:
                staged.put_entity(scope, entity)
            for edge in batch.relationships:
                staged.put_relationship(scope, edge)
            validate_hierarchy(
                [value for (key, _), value in staged._entities.items() if key == scope],
                [
                    value
                    for (key, _), value in staged._relationships.items()
                    if key == scope
                ],
            )
            self._entities, self._relationships, self._adjacency = (
                staged._entities,
                staged._relationships,
                staged._adjacency,
            )
            self._operations[(scope, batch.operation_id)] = batch.digest

    @_locked
    def put_entity(self, scope: Scope, entity: TopologyEntity) -> None:
        self._entities[(scope, entity.id)] = entity

    @_locked
    def put_relationship(
        self, scope: Scope, relationship: TopologyRelationship
    ) -> None:
        if (scope, relationship.source_id) not in self._entities:
            raise ValueError(
                f"source entity {relationship.source_id!r} does not exist in scope"
            )
        if (scope, relationship.target_id) not in self._entities:
            raise ValueError(
                f"target entity {relationship.target_id!r} does not exist in scope"
            )
        key = scope, relationship.id
        previous = self._relationships.get(key)
        if previous:
            self._adjacency[(scope, previous.source_id)].discard(previous.id)
            self._adjacency[(scope, previous.target_id)].discard(previous.id)
        self._relationships[key] = relationship
        self._adjacency[(scope, relationship.source_id)].add(relationship.id)
        self._adjacency[(scope, relationship.target_id)].add(relationship.id)

    @_locked
    def remove_entity(self, scope: Scope, entity_id: str) -> bool:
        if (scope, entity_id) not in self._entities:
            return False
        for relationship_id in tuple(self._adjacency.get((scope, entity_id), ())):
            self.remove_relationship(scope, relationship_id)
        self._adjacency.pop((scope, entity_id), None)
        del self._entities[(scope, entity_id)]
        return True

    @_locked
    def remove_relationship(self, scope: Scope, relationship_id: str) -> bool:
        relationship = self._relationships.pop((scope, relationship_id), None)
        if relationship is None:
            return False
        self._adjacency[(scope, relationship.source_id)].discard(relationship.id)
        self._adjacency[(scope, relationship.target_id)].discard(relationship.id)
        return True

    @_locked
    def search(self, query: TopologyQuery) -> TopologyResult:
        matches = sorted(
            (
                entity
                for (scope, _), entity in self._entities.items()
                if scope == query.scope
                and (not query.ids or entity.id in query.ids)
                and (not query.kinds or entity.kind in query.kinds)
                and (not query.statuses or entity.status in query.statuses)
                and entity.confidence >= query.minimum_confidence
                and (query.include_stale or not entity.stale)
                and all(
                    entity.properties.get(key) == value
                    for key, value in query.properties.items()
                )
            ),
            key=lambda entity: entity.id,
        )
        entities = tuple(matches[query.offset : query.offset + query.limit])
        return TopologyResult(
            entities=entities,
            total=len(matches),
            has_more=query.offset + len(entities) < len(matches),
        )

    @_locked
    def get_relationships(
        self,
        scope: Scope,
        entity_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[TopologyRelationship, ...]:
        return tuple(
            sorted(
                (
                    relationship
                    for (
                        relationship_scope,
                        _,
                    ), relationship in self._relationships.items()
                    if relationship_scope == scope
                    and (not statuses or relationship.status in statuses)
                    and relationship.source_id in entity_ids
                    and relationship.target_id in entity_ids
                ),
                key=lambda relationship: relationship.id,
            )
        )

    @_locked
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
                break
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
