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
    KnowledgeLink,
    KnowledgeObject,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeResult,
    KnowledgeSubgraph,
    KnowledgeTraversal,
)

metadata = MetaData()
scope_type = Text().with_variant(String(500), "mysql")
knowledge_table = Table(
    "knowledge_objects",
    metadata,
    Column("scope", scope_type, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("kind", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("summary", Text, nullable=False),
    Column("properties", Text, nullable=False),
)
link_table = Table(
    "knowledge_links",
    metadata,
    Column("scope", scope_type, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("knowledge_id", String(255), nullable=False),
    Column("target_kind", String(64), nullable=False),
    Column("target_id", String(500), nullable=False),
    Column("properties", Text, nullable=False),
)

relationship_table = Table(
    "knowledge_relationships",
    metadata,
    Column("scope", scope_type, primary_key=True),
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

    def put(self, scope: Scope, knowledge: KnowledgeObject) -> None:
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

    def remove(self, scope: Scope, knowledge_id: str) -> None:
        key = self._scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope == key,
                        link_table.c.knowledge_id == knowledge_id,
                    )
                )
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope == key,
                        (relationship_table.c.source_id == knowledge_id)
                        | (relationship_table.c.target_id == knowledge_id),
                    )
                )
                connection.execute(
                    delete(knowledge_table).where(
                        knowledge_table.c.scope == key,
                        knowledge_table.c.id == knowledge_id,
                    )
                )
            self._refresh()

    def put_link(self, scope: Scope, link: KnowledgeLink) -> None:
        key = self._scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                known = connection.execute(
                    select(knowledge_table.c.id).where(
                        knowledge_table.c.scope == key,
                        knowledge_table.c.id == link.knowledge_id,
                    )
                ).first()
                if known is None:
                    raise ValueError(
                        "a link needs a knowledge object in the same scope"
                    )
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope == key, link_table.c.id == link.id
                    )
                )
                connection.execute(
                    link_table.insert().values(
                        scope=key,
                        id=link.id,
                        knowledge_id=link.knowledge_id,
                        target_kind=link.target_kind,
                        target_id=link.target_id,
                        properties=self._encode(link.properties),
                    )
                )

    def get_links(
        self, scope: Scope, knowledge_ids: frozenset[str]
    ) -> tuple[KnowledgeLink, ...]:
        key = self._scope_key(scope)
        statement = select(link_table).where(link_table.c.scope == key)
        if knowledge_ids:
            statement = statement.where(
                link_table.c.knowledge_id.in_(sorted(knowledge_ids))
            )
        with self._engine.connect() as connection:
            return tuple(
                KnowledgeLink(
                    id=row["id"],
                    knowledge_id=row["knowledge_id"],
                    target_kind=row["target_kind"],
                    target_id=row["target_id"],
                    properties=json.loads(row["properties"]),
                )
                for row in connection.execute(
                    statement.order_by(link_table.c.id)
                ).mappings()
            )

    def knowledge_ids_for_paths(
        self, scope: Scope, paths: frozenset[str]
    ) -> frozenset[str]:
        if not paths:
            return frozenset()
        key = self._scope_key(scope)
        with self._engine.connect() as connection:
            return frozenset(
                row[0]
                for row in connection.execute(
                    select(link_table.c.knowledge_id).where(
                        link_table.c.scope == key,
                        link_table.c.target_id.in_(sorted(paths)),
                    )
                )
            )

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
                    KnowledgeObject(
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
