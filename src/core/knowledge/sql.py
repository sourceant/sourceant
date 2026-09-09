from __future__ import annotations

import json
from collections.abc import Mapping
from fnmatch import fnmatchcase
from heapq import nsmallest
from threading import RLock
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Engine,
    MetaData,
    String,
    Table,
    Text,
    case,
    cast,
    delete,
    select,
    type_coerce,
)

from src.core import scopes
from src.core.scope import Scope
from src.core.sql_support import chunked, rows_for

from .characteristics import KnowledgeImportance
from .memory import InMemoryKnowledgeRepository
from .models import (
    KnowledgeLink,
    KnowledgeObject,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeResult,
    KnowledgeSelection,
    KnowledgeSubgraph,
    KnowledgeTraversal,
)

metadata = MetaData()
knowledge_table = Table(
    "knowledge_objects",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("kind", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("summary", Text, nullable=False),
    Column("properties", Text, nullable=False),
)
link_table = Table(
    "knowledge_links",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("knowledge_id", String(255), nullable=False),
    Column("target_kind", String(64), nullable=False),
    Column("target_id", String(255), nullable=False),
    Column("properties", Text, nullable=False),
)

relationship_table = Table(
    "knowledge_relationships",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
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
            scopes.ensure(engine)
            metadata.create_all(engine)

    def put(self, scope: Scope, knowledge: KnowledgeObject) -> None:
        values = {
            "id": knowledge.id,
            "kind": knowledge.kind,
            "status": knowledge.status,
            "summary": knowledge.summary,
            "properties": self._encode(knowledge.properties),
        }
        with self._lock:
            with self._engine.begin() as connection:
                values["scope_id"] = scopes.remembered(connection, scope)
                connection.execute(
                    delete(knowledge_table).where(
                        knowledge_table.c.scope_id == values["scope_id"],
                        knowledge_table.c.id == knowledge.id,
                    )
                )
                connection.execute(knowledge_table.insert().values(**values))
            self._refresh()

    def put_relationship(
        self, scope: Scope, relationship: KnowledgeRelationship
    ) -> None:
        values = {
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
                values["scope_id"] = scopes.remembered(connection, scope)
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == values["scope_id"],
                        relationship_table.c.id == relationship.id,
                    )
                )
                connection.execute(relationship_table.insert().values(**values))
            self._refresh()

    def remove_relationship(self, scope: Scope, relationship_id: str) -> bool:
        key = scopes.known_id(self._engine, scope)
        with self._lock:
            with self._engine.begin() as connection:
                result = connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == key,
                        relationship_table.c.id == relationship_id,
                    )
                )
            self._refresh()
        return result.rowcount > 0

    def remove(self, scope: Scope, knowledge_id: str) -> None:
        key = scopes.known_id(self._engine, scope)
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope_id == key,
                        link_table.c.knowledge_id == knowledge_id,
                    )
                )
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == key,
                        (relationship_table.c.source_id == knowledge_id)
                        | (relationship_table.c.target_id == knowledge_id),
                    )
                )
                connection.execute(
                    delete(knowledge_table).where(
                        knowledge_table.c.scope_id == key,
                        knowledge_table.c.id == knowledge_id,
                    )
                )
            self._refresh()

    def put_link(self, scope: Scope, link: KnowledgeLink) -> None:
        with self._lock:
            with self._engine.begin() as connection:
                key = scopes.remembered(connection, scope)
                known = connection.execute(
                    select(knowledge_table.c.id).where(
                        knowledge_table.c.scope_id == key,
                        knowledge_table.c.id == link.knowledge_id,
                    )
                ).first()
                if known is None:
                    raise ValueError(
                        "a link needs a knowledge object in the same scope"
                    )
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope_id == key, link_table.c.id == link.id
                    )
                )
                connection.execute(
                    link_table.insert().values(
                        scope_id=key,
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
        key = scopes.known_id(self._engine, scope)
        with self._engine.connect() as connection:
            if not knowledge_ids:
                rows = list(
                    connection.execute(
                        select(link_table)
                        .where(link_table.c.scope_id == key)
                        .order_by(link_table.c.id)
                    ).mappings()
                )
            else:
                rows = rows_for(
                    knowledge_ids,
                    lambda chunk: connection.execute(
                        select(link_table).where(
                            link_table.c.scope_id == key,
                            link_table.c.knowledge_id.in_(chunk),
                        )
                    ).mappings(),
                )
        found = {
            row["id"]: KnowledgeLink(
                id=row["id"],
                knowledge_id=row["knowledge_id"],
                target_kind=row["target_kind"],
                target_id=row["target_id"],
                properties=json.loads(row["properties"]),
            )
            for row in rows
        }
        return tuple(found[key] for key in sorted(found))

    def knowledge_ids_for_paths(
        self, scope: Scope, paths: frozenset[str]
    ) -> frozenset[str]:
        if not paths:
            return frozenset()
        key = scopes.known_id(self._engine, scope)
        with self._engine.connect() as connection:
            return frozenset(
                row[0]
                for row in rows_for(
                    paths,
                    lambda chunk: connection.execute(
                        select(link_table.c.knowledge_id).where(
                            link_table.c.scope_id == key,
                            link_table.c.target_id.in_(chunk),
                        )
                    ),
                )
            )

    def select(self, selection: KnowledgeSelection) -> tuple[KnowledgeObject, ...]:
        properties = (
            cast(knowledge_table.c.properties, JSON)
            if self._engine.dialect.name == "postgresql"
            else type_coerce(knowledge_table.c.properties, JSON)
        )
        importance = case(
            {level.value: level.priority for level in KnowledgeImportance},
            value=properties["importance"].as_string(),
            else_=KnowledgeImportance.NORMAL.priority,
        )
        id_order = knowledge_table.c.id.collate(
            {"postgresql": "C", "mysql": "utf8mb4_bin"}.get(
                self._engine.dialect.name, "BINARY"
            )
        )
        selected: dict[str, KnowledgeObject] = {}

        def retain(rows):
            for row in rows:
                item = KnowledgeObject(
                    row["id"],
                    row["kind"],
                    row["status"],
                    row["summary"],
                    json.loads(row["properties"]),
                )
                selected[item.id] = item
            best = nsmallest(
                selection.limit,
                selected.values(),
                key=lambda item: (-item.importance.priority, item.id),
            )
            selected.clear()
            selected.update((item.id, item) for item in best)

        with self._engine.connect() as connection:
            key = scopes.known(connection, selection.scope)
            if key is None:
                return ()
            ranked = (
                select(knowledge_table)
                .where(
                    knowledge_table.c.scope_id == key,
                    knowledge_table.c.status.in_(("active", "accepted", "approved")),
                )
                .order_by(importance.desc(), id_order)
            )
            retain(
                connection.execute(
                    ranked.where(
                        properties["applicability"].as_string() == "scope"
                    ).limit(selection.limit)
                ).mappings()
            )
            for paths in chunked(selection.paths):
                linked = select(link_table.c.knowledge_id).where(
                    link_table.c.scope_id == key,
                    link_table.c.target_id.in_(paths),
                )
                retain(
                    connection.execute(
                        ranked.where(knowledge_table.c.id.in_(linked)).limit(
                            selection.limit
                        )
                    ).mappings()
                )
            if selection.paths:
                # Glob semantics stay identical across SQL dialects. Stream only
                # path-bearing candidates in rank order and stop after enough matches.
                candidates = ranked.where(properties["paths"].as_string().is_not(None))
                with connection.execution_options(yield_per=100).execute(
                    candidates
                ) as rows:
                    matched = 0
                    for row in rows.mappings():
                        patterns = json.loads(row["properties"]).get("paths", ())
                        if any(
                            path == pattern
                            or path.startswith(pattern.rstrip("/") + "/")
                            or fnmatchcase(path, pattern)
                            for path in selection.paths
                            for pattern in patterns
                        ):
                            retain((row,))
                            matched += 1
                            if matched == selection.limit:
                                break
        return tuple(selected.values())

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
            for row in connection.execute(
                scopes.with_scope(knowledge_table)
            ).mappings():
                memory.put(
                    scopes.read(row["qualifiers"]),
                    KnowledgeObject(
                        row["id"],
                        row["kind"],
                        row["status"],
                        row["summary"],
                        json.loads(row["properties"]),
                    ),
                )
            for row in connection.execute(
                scopes.with_scope(relationship_table)
            ).mappings():
                memory.put_relationship(
                    scopes.read(row["qualifiers"]),
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
    def _encode(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
