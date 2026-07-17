from collections import defaultdict, deque

from src.core.scope import Scope

from .models import (
    Knowledge,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeResult,
    KnowledgeSubgraph,
    KnowledgeTraversal,
)


class InMemoryKnowledgeRepository:
    def __init__(self) -> None:
        self._knowledge: dict[tuple[Scope, str], Knowledge] = {}
        self._relationships: dict[tuple[Scope, str], KnowledgeRelationship] = {}
        self._adjacency: dict[tuple[Scope, str], set[str]] = defaultdict(set)

    def put(self, scope: Scope, knowledge: Knowledge) -> None:
        self._knowledge[(scope, knowledge.id)] = knowledge

    def put_relationship(
        self, scope: Scope, relationship: KnowledgeRelationship
    ) -> None:
        if (scope, relationship.source_id) not in self._knowledge or (
            scope,
            relationship.target_id,
        ) not in self._knowledge:
            raise ValueError("relationship endpoints must exist in the same scope")
        key = scope, relationship.id
        previous = self._relationships.get(key)
        if previous:
            self._adjacency[(scope, previous.source_id)].discard(previous.id)
            self._adjacency[(scope, previous.target_id)].discard(previous.id)
        self._relationships[key] = relationship
        self._adjacency[(scope, relationship.source_id)].add(relationship.id)
        self._adjacency[(scope, relationship.target_id)].add(relationship.id)

    def search(self, query: KnowledgeQuery) -> KnowledgeResult:
        matches = [
            knowledge
            for (scope, _), knowledge in self._knowledge.items()
            if scope == query.scope
            and (not query.ids or knowledge.id in query.ids)
            and (not query.kinds or knowledge.kind in query.kinds)
            and (not query.statuses or knowledge.status in query.statuses)
            and all(
                knowledge.properties.get(key) == value
                for key, value in query.properties.items()
            )
        ]
        items = tuple(matches[query.offset : query.offset + query.limit])
        return KnowledgeResult(
            items=items,
            total=len(matches),
            has_more=query.offset + len(items) < len(matches),
        )

    def get_relationships(
        self,
        scope: Scope,
        knowledge_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[KnowledgeRelationship, ...]:
        return tuple(
            relationship
            for (relationship_scope, _), relationship in self._relationships.items()
            if relationship_scope == scope
            and (not statuses or relationship.status in statuses)
            and relationship.source_id in knowledge_ids
            and relationship.target_id in knowledge_ids
        )

    def traverse(self, traversal: KnowledgeTraversal) -> KnowledgeSubgraph:
        scope = traversal.scope
        queue = deque(
            (item, 0)
            for knowledge_id in traversal.knowledge_ids
            if (item := self._knowledge.get((scope, knowledge_id)))
            and (
                not traversal.knowledge_statuses
                or item.status in traversal.knowledge_statuses
            )
        )
        queued = {item.id for item, _ in queue}
        visited: set[str] = set()
        items: list[Knowledge] = []
        relationships: dict[str, KnowledgeRelationship] = {}
        truncated = False

        while queue:
            item, distance = queue.popleft()
            if item.id in visited:
                continue
            if len(items) >= traversal.knowledge_limit:
                truncated = True
                continue
            visited.add(item.id)
            items.append(item)
            if distance == traversal.depth:
                continue

            for relationship_id in sorted(self._adjacency.get((scope, item.id), ())):
                relationship = self._relationships[(scope, relationship_id)]
                if (
                    traversal.relationship_types
                    and relationship.type not in traversal.relationship_types
                ):
                    continue
                if (
                    traversal.relationship_statuses
                    and relationship.status not in traversal.relationship_statuses
                ):
                    continue
                if (
                    traversal.direction == "outbound"
                    and relationship.source_id != item.id
                ):
                    continue
                if (
                    traversal.direction == "inbound"
                    and relationship.target_id != item.id
                ):
                    continue
                other_id = (
                    relationship.target_id
                    if relationship.source_id == item.id
                    else relationship.source_id
                )
                target = self._knowledge.get((scope, other_id))
                if not target or (
                    traversal.knowledge_statuses
                    and target.status not in traversal.knowledge_statuses
                ):
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
        return KnowledgeSubgraph(
            items=tuple(items),
            relationships=packed_relationships,
            truncated=truncated or len(packed_relationships) != len(relationships),
        )
