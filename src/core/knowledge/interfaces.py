from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import Knowledge, KnowledgeQuery, KnowledgeRelationship, KnowledgeResult


@runtime_checkable
class KnowledgeReader(Protocol):
    def search(self, query: KnowledgeQuery) -> KnowledgeResult: ...

    def get_relationships(
        self,
        scope: Scope,
        knowledge_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[KnowledgeRelationship, ...]: ...


@runtime_checkable
class KnowledgeWriter(Protocol):
    def put(self, scope: Scope, knowledge: Knowledge) -> None: ...

    def put_relationship(
        self, scope: Scope, relationship: KnowledgeRelationship
    ) -> None: ...


@runtime_checkable
class KnowledgeRepository(KnowledgeReader, KnowledgeWriter, Protocol):
    pass
