from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from threading import RLock
from typing import Any

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


class SQLiteKnowledgeRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if self._path != Path(":memory:"):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = RLock()
        self._memory = InMemoryKnowledgeRepository()
        self._create_schema()
        self._load()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def put(self, scope: Scope, knowledge: Knowledge) -> None:
        properties = self._encode(knowledge.properties)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO knowledge (scope, id, kind, status, summary, properties)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (scope, id) DO UPDATE SET
                    kind = excluded.kind,
                    status = excluded.status,
                    summary = excluded.summary,
                    properties = excluded.properties
                """,
                (
                    self._scope_key(scope),
                    knowledge.id,
                    knowledge.kind,
                    knowledge.status,
                    knowledge.summary,
                    properties,
                ),
            )
            self._memory.put(scope, knowledge)

    def put_relationship(
        self, scope: Scope, relationship: KnowledgeRelationship
    ) -> None:
        properties = self._encode(relationship.properties)
        with self._lock:
            self._memory.put_relationship(scope, relationship)
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO relationships
                        (scope, id, source_id, target_id, type, status, properties)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (scope, id) DO UPDATE SET
                        source_id = excluded.source_id,
                        target_id = excluded.target_id,
                        type = excluded.type,
                        status = excluded.status,
                        properties = excluded.properties
                    """,
                    (
                        self._scope_key(scope),
                        relationship.id,
                        relationship.source_id,
                        relationship.target_id,
                        relationship.type,
                        relationship.status,
                        properties,
                    ),
                )

    def search(self, query: KnowledgeQuery) -> KnowledgeResult:
        with self._lock:
            return self._memory.search(query)

    def get_relationships(
        self,
        scope: Scope,
        knowledge_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[KnowledgeRelationship, ...]:
        with self._lock:
            return self._memory.get_relationships(scope, knowledge_ids, statuses)

    def traverse(self, traversal: KnowledgeTraversal) -> KnowledgeSubgraph:
        with self._lock:
            return self._memory.traverse(traversal)

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    scope TEXT NOT NULL,
                    id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    properties TEXT NOT NULL,
                    PRIMARY KEY (scope, id)
                );
                CREATE TABLE IF NOT EXISTS relationships (
                    scope TEXT NOT NULL,
                    id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    properties TEXT NOT NULL,
                    PRIMARY KEY (scope, id)
                );
                """)

    def _load(self) -> None:
        with self._lock:
            for row in self._connection.execute(
                "SELECT scope, id, kind, status, summary, properties FROM knowledge"
            ):
                scope, identifier, kind, status, summary, properties = row
                self._memory.put(
                    self._decode_scope(scope),
                    Knowledge(
                        identifier,
                        kind,
                        status,
                        summary,
                        json.loads(properties),
                    ),
                )
            for row in self._connection.execute("""
                SELECT scope, id, source_id, target_id, type, status, properties
                FROM relationships
                """):
                scope, identifier, source, target, kind, status, properties = row
                self._memory.put_relationship(
                    self._decode_scope(scope),
                    KnowledgeRelationship(
                        identifier,
                        source,
                        target,
                        kind,
                        status,
                        json.loads(properties),
                    ),
                )

    @staticmethod
    def _scope_key(scope: Scope) -> str:
        return json.dumps(scope.values, separators=(",", ":"))

    @staticmethod
    def _decode_scope(value: str) -> Scope:
        return Scope(tuple(tuple(item) for item in json.loads(value)))

    @staticmethod
    def _encode(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
