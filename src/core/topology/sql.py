from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from threading import RLock
from typing import Any

from sqlalchemy import (
    BigInteger,
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

from src.core import scopes
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
entity_table = Table(
    "topology_entities",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
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
    Column("scope_id", BigInteger, primary_key=True),
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


operation_table = Table(
    "topology_operations",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("digest", String(64), nullable=False),
)


class SQLTopologyRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        self._memory = InMemoryTopologyRepository()
        if create_schema:
            scopes.ensure(engine)
            metadata.create_all(engine)
        self._refresh()

    def apply_batch(self, scope, batch):
        from .batch import validate_hierarchy

        with self._lock, self._engine.begin() as connection:
            scope_id = scopes.remembered(connection, scope)
            connection.execute(
                select(scopes.scope_table)
                .where(scopes.scope_table.c.id == scope_id)
                .with_for_update()
            ).first()
            previous = connection.execute(
                select(operation_table.c.digest).where(
                    operation_table.c.scope_id == scope_id,
                    operation_table.c.id == batch.operation_id,
                )
            ).scalar_one_or_none()
            if previous is not None:
                if previous != batch.digest:
                    raise ValueError(
                        "Operation ID was already used for another request"
                    )
                return
            for identifier in batch.remove_relationships:
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == scope_id,
                        relationship_table.c.id == identifier,
                    )
                )
            for entity in batch.entities:
                connection.execute(
                    delete(entity_table).where(
                        entity_table.c.scope_id == scope_id,
                        entity_table.c.id == entity.id,
                    )
                )
                connection.execute(
                    entity_table.insert().values(
                        scope_id=scope_id,
                        id=entity.id,
                        kind=entity.kind,
                        status=entity.status,
                        confidence=entity.confidence,
                        stale=entity.stale,
                        properties=self._encode(entity.properties),
                        evidence=self._encode_evidence(entity.evidence),
                    )
                )
            ids = set(
                connection.execute(
                    select(entity_table.c.id).where(entity_table.c.scope_id == scope_id)
                ).scalars()
            )
            for edge in batch.relationships:
                if edge.source_id not in ids or edge.target_id not in ids:
                    raise ValueError(
                        "Relationship endpoints must exist in the same scope"
                    )
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == scope_id,
                        relationship_table.c.id == edge.id,
                    )
                )
                connection.execute(
                    relationship_table.insert().values(
                        scope_id=scope_id,
                        id=edge.id,
                        source_id=edge.source_id,
                        target_id=edge.target_id,
                        type=edge.type,
                        status=edge.status,
                        confidence=edge.confidence,
                        stale=edge.stale,
                        properties=self._encode(edge.properties),
                        evidence=self._encode_evidence(edge.evidence),
                    )
                )
            edges = [
                TopologyRelationship(
                    row.id, row.source_id, row.target_id, row.type, row.status
                )
                for row in connection.execute(
                    select(relationship_table).where(
                        relationship_table.c.scope_id == scope_id
                    )
                )
            ]
            validate_hierarchy((), edges)
            connection.execute(
                operation_table.insert().values(
                    scope_id=scope_id, id=batch.operation_id, digest=batch.digest
                )
            )
        self._refresh()

    def put_entity(self, scope: Scope, entity: TopologyEntity) -> None:
        values = {
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
                values["scope_id"] = scopes.remembered(connection, scope)
                connection.execute(
                    delete(entity_table).where(
                        entity_table.c.scope_id == values["scope_id"],
                        entity_table.c.id == entity.id,
                    )
                )
                connection.execute(entity_table.insert().values(**values))
            self._refresh()

    def put_relationship(
        self, scope: Scope, relationship: TopologyRelationship
    ) -> None:
        values = {
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
                values["scope_id"] = scopes.remembered(connection, scope)
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == values["scope_id"],
                        relationship_table.c.id == relationship.id,
                    )
                )
                connection.execute(relationship_table.insert().values(**values))
            self._refresh()

    def remove_entity(self, scope: Scope, entity_id: str) -> bool:
        scope_key = scopes.known_id(self._engine, scope)
        with self._lock:
            self._refresh()
            if not self._memory.remove_entity(scope, entity_id):
                return False
            with self._engine.begin() as connection:
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == scope_key,
                        (relationship_table.c.source_id == entity_id)
                        | (relationship_table.c.target_id == entity_id),
                    )
                )
                connection.execute(
                    delete(entity_table).where(
                        entity_table.c.scope_id == scope_key,
                        entity_table.c.id == entity_id,
                    )
                )
            self._refresh()
            return True

    def remove_relationship(self, scope: Scope, relationship_id: str) -> bool:
        scope_key = scopes.known_id(self._engine, scope)
        with self._lock:
            self._refresh()
            if not self._memory.remove_relationship(scope, relationship_id):
                return False
            with self._engine.begin() as connection:
                connection.execute(
                    delete(relationship_table).where(
                        relationship_table.c.scope_id == scope_key,
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

    def get_relationship(
        self, scope: Scope, relationship_id: str
    ) -> TopologyRelationship | None:
        with self._lock:
            self._refresh()
            return self._memory.get_relationship(scope, relationship_id)

    def traverse(self, traversal: TopologyTraversal) -> TopologySubgraph:
        with self._lock:
            self._refresh()
            return self._memory.traverse(traversal)

    def close(self) -> None:
        self._engine.dispose()

    def _refresh(self) -> None:
        memory = InMemoryTopologyRepository()
        with self._engine.connect() as connection:
            for row in connection.execute(scopes.with_scope(entity_table)).mappings():
                memory.put_entity(
                    scopes.read(row["qualifiers"]),
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
            for row in connection.execute(
                scopes.with_scope(relationship_table)
            ).mappings():
                memory.put_relationship(
                    scopes.read(row["qualifiers"]),
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
