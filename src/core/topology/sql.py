from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from threading import RLock
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Engine,
    Float,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
)

from src.core.scope import Scope

from .memory import InMemoryTopologyRepository
from .models import (
    TopologyEntity,
    TopologyEvidence,
    TopologyQuery,
    TopologyRelationship,
    TopologyResult,
    TopologySubgraph,
    TopologyTraversal,
)

metadata = MetaData()
scope_type = Text().with_variant(String(191), "mysql")
entity_table = Table(
    "topology_entities",
    metadata,
    Column("scope", scope_type, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("kind", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("stale", Boolean, nullable=False),
    Column("properties", Text, nullable=False),
    Column("evidence", Text, nullable=False),
)
relationship_table = Table(
    "topology_relationships",
    metadata,
    Column("scope", scope_type, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("source_id", String(255), nullable=False),
    Column("target_id", String(255), nullable=False),
    Column("type", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("stale", Boolean, nullable=False),
    Column("properties", Text, nullable=False),
    Column("evidence", Text, nullable=False),
)


class SQLTopologyRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        self._memory = InMemoryTopologyRepository()
        if create_schema:
            metadata.create_all(engine)
        self._refresh()

    def put_entity(self, scope: Scope, entity: TopologyEntity) -> None:
        values = {
            "scope": self._scope_key(scope),
            "id": entity.id,
            "kind": entity.kind,
            "status": entity.status,
            "confidence": entity.confidence,
            "stale": entity.stale,
            "properties": self._encode(entity.properties),
            "evidence": self._encode_evidence(entity.evidence),
        }
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(entity_table).where(
                        entity_table.c.scope == values["scope"],
                        entity_table.c.id == entity.id,
                    )
                )
                connection.execute(entity_table.insert().values(**values))
            self._refresh()

    def put_relationship(
        self, scope: Scope, relationship: TopologyRelationship
    ) -> None:
        values = {
            "scope": self._scope_key(scope),
            "id": relationship.id,
            "source_id": relationship.source_id,
            "target_id": relationship.target_id,
            "type": relationship.type,
            "status": relationship.status,
            "confidence": relationship.confidence,
            "stale": relationship.stale,
            "properties": self._encode(relationship.properties),
            "evidence": self._encode_evidence(relationship.evidence),
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

    def remove_entity(self, scope: Scope, entity_id: str) -> bool:
        scope_key = self._scope_key(scope)
        with self._lock:
            self._refresh()
            if not self._memory.remove_entity(scope, entity_id):
                return False
            with self._engine.begin() as connection:
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope == scope_key,
                        (relationship_table.c.source_id == entity_id)
                        | (relationship_table.c.target_id == entity_id),
                    )
                )
                connection.execute(
                    delete(entity_table).where(
                        entity_table.c.scope == scope_key,
                        entity_table.c.id == entity_id,
                    )
                )
            self._refresh()
            return True

    def remove_relationship(self, scope: Scope, relationship_id: str) -> bool:
        scope_key = self._scope_key(scope)
        with self._lock:
            self._refresh()
            if not self._memory.remove_relationship(scope, relationship_id):
                return False
            with self._engine.begin() as connection:
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope == scope_key,
                        relationship_table.c.id == relationship_id,
                    )
                )
            self._refresh()
            return True

    def search(self, query: TopologyQuery) -> TopologyResult:
        with self._lock:
            self._refresh()
            return self._memory.search(query)

    def get_relationships(
        self,
        scope: Scope,
        entity_ids: frozenset[str],
        statuses: frozenset[str] = frozenset(),
    ) -> tuple[TopologyRelationship, ...]:
        with self._lock:
            self._refresh()
            return self._memory.get_relationships(scope, entity_ids, statuses)

    def traverse(self, traversal: TopologyTraversal) -> TopologySubgraph:
        with self._lock:
            self._refresh()
            return self._memory.traverse(traversal)

    def close(self) -> None:
        self._engine.dispose()

    def _refresh(self) -> None:
        memory = InMemoryTopologyRepository()
        with self._engine.connect() as connection:
            for row in connection.execute(select(entity_table)).mappings():
                memory.put_entity(
                    self._decode_scope(row["scope"]),
                    TopologyEntity(
                        row["id"],
                        row["kind"],
                        row["status"],
                        row["confidence"],
                        bool(row["stale"]),
                        json.loads(row["properties"]),
                        self._decode_evidence(row["evidence"]),
                    ),
                )
            for row in connection.execute(select(relationship_table)).mappings():
                memory.put_relationship(
                    self._decode_scope(row["scope"]),
                    TopologyRelationship(
                        row["id"],
                        row["source_id"],
                        row["target_id"],
                        row["type"],
                        row["status"],
                        row["confidence"],
                        bool(row["stale"]),
                        json.loads(row["properties"]),
                        self._decode_evidence(row["evidence"]),
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

    @classmethod
    def _encode_evidence(cls, evidence: Sequence[TopologyEvidence]) -> str:
        return json.dumps(
            [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "source": item.source,
                    "revision": item.revision,
                    "properties": item.properties,
                }
                for item in evidence
            ],
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _decode_evidence(value: str) -> tuple[TopologyEvidence, ...]:
        return tuple(
            TopologyEvidence(
                item["id"],
                item["kind"],
                item["source"],
                item.get("revision", ""),
                item.get("properties", {}),
            )
            for item in json.loads(value)
        )
