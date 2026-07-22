from __future__ import annotations

import json
from collections.abc import Mapping
from threading import RLock
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
)

from src.core.scope import Scope

from .memory import InMemoryKnowledgeRepository
from .models import (
    Knowledge,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeResult,
    KnowledgeSubgraph,
    KnowledgeTraversal,
)

metadata = MetaData()
knowledge_table = Table(
    "knowledge",
    metadata,
    Column("scope", Text, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("kind", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("summary", Text, nullable=False),
    Column("properties", Text, nullable=False),
)
relationship_table = Table(
    "knowledge_relationships",
    metadata,
    Column("scope", Text, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("source_id", String(255), nullable=False),
    Column("target_id", String(255), nullable=False),
    Column("type", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("properties", Text, nullable=False),
)


class SQLKnowledgeRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        self._memory = InMemoryKnowledgeRepository()
        if create_schema:
            metadata.create_all(engine)
        self._refresh()

    def put(self, scope: Scope, knowledge: Knowledge) -> None:
        values = {
            "scope": self._scope_key(scope),
            "id": knowledge.id,
            "kind": knowledge.kind,
            "status": knowledge.status,
            "summary": knowledge.summary,
            "properties": self._encode(knowledge.properties),
        }
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(knowledge_table).where(
                        knowledge_table.c.scope == values["scope"],
                        knowledge_table.c.id == knowledge.id,
                    )
                )
                connection.execute(knowledge_table.insert().values(**values))
            self._refresh()

    def put_relationship(
        self, scope: Scope, relationship: KnowledgeRelationship
    ) -> None:
        values = {
            "scope": self._scope_key(scope),
            "id": relationship.id,
            "source_id": relationship.source_id,
            "target_id": relationship.target_id,
            "type": relationship.type,
            "status": relationship.status,
            "properties": self._encode(relationship.properties),
        }
        with self._lock:
            self._refresh()
            self._memory.put_relationship(scope, relationship)
            with self._engine.begin() as connection:
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope == values["scope"],
                        relationship_table.c.id == relationship.id,
                    )
                )
                connection.execute(relationship_table.insert().values(**values))
            self._refresh()

    def search(self, query: KnowledgeQuery) -> KnowledgeResult:
        with self._lock:
            self._refresh()
            return self._memory.search(query)

    def get_relationships(
        self,
        scope: Scope,
        knowledge_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[KnowledgeRelationship, ...]:
        with self._lock:
            self._refresh()
            return self._memory.get_relationships(scope, knowledge_ids, statuses)

    def traverse(self, traversal: KnowledgeTraversal) -> KnowledgeSubgraph:
        with self._lock:
            self._refresh()
            return self._memory.traverse(traversal)

    def _refresh(self) -> None:
        memory = InMemoryKnowledgeRepository()
        with self._engine.connect() as connection:
            for row in connection.execute(select(knowledge_table)).mappings():
                memory.put(
                    self._decode_scope(row["scope"]),
                    Knowledge(
                        row["id"],
                        row["kind"],
                        row["status"],
                        row["summary"],
                        json.loads(row["properties"]),
                    ),
                )
            for row in connection.execute(select(relationship_table)).mappings():
                memory.put_relationship(
                    self._decode_scope(row["scope"]),
                    KnowledgeRelationship(
                        row["id"],
                        row["source_id"],
                        row["target_id"],
                        row["type"],
                        row["status"],
                        json.loads(row["properties"]),
                    ),
                )
        self._memory = memory

    @staticmethod
    def _scope_key(scope: Scope) -> str:
        return json.dumps(scope.values, separators=(",", ":"))

    @staticmethod
    def _decode_scope(value: str) -> Scope:
        return Scope(tuple(tuple(item) for item in json.loads(value)))

    @staticmethod
    def _encode(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
