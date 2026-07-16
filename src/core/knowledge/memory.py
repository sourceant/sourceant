from src.core.scope import Scope

from .models import Knowledge, KnowledgeQuery, KnowledgeRelationship, KnowledgeResult


class InMemoryKnowledgeRepository:
    def __init__(self) -> None:
        self._knowledge: dict[tuple[Scope, str], Knowledge] = {}
        self._relationships: dict[tuple[Scope, str], KnowledgeRelationship] = {}

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
        self._relationships[(scope, relationship.id)] = relationship

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
