from __future__ import annotations

import json
from threading import RLock
from typing import Any, Mapping

from sqlalchemy import (
    Boolean,
    Column,
    Engine,
    Float,
    Index,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
)

from src.core.scope import Scope
from src.core.sql_support import rows_for
from src.core.topology import TopologyEvidence

from .models import (
    ChangedCodeReference,
    CompatibilityCheck,
    CompatibilityCheckQuery,
)

metadata = MetaData()
# Sized for MySQL, which allows 3072 bytes in a key and reserves four per
# character. A column wider here would accept what the database then refuses.
scope_key_type = String(191)
name_type = String(191)

mapping_table = Table(
    "impact_code_mappings",
    metadata,
    Column("scope", scope_key_type, primary_key=True),
    Column("change_kind", String(64), primary_key=True),
    Column("change_id", name_type, primary_key=True),
    Column("revision", String(64), primary_key=True),
    Column("entity_id", name_type, primary_key=True),
    Index("ix_impact_code_mappings_scope_change", "scope", "change_kind", "change_id"),
)

check_table = Table(
    "compatibility_checks",
    metadata,
    Column("scope", scope_key_type, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("provider_entity_id", String(255), nullable=False),
    Column("consumer_entity_id", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("compatible", Boolean, nullable=True),
    Column("before_revision", String(255), nullable=False),
    Column("after_revision", String(255), nullable=False),
    Column("summary", Text, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("stale", Boolean, nullable=False),
    Column("evidence", Text, nullable=False),
    Column("properties", Text, nullable=False),
    Index("ix_compatibility_checks_scope_provider", "scope", "provider_entity_id"),
    Index("ix_compatibility_checks_scope_consumer", "scope", "consumer_entity_id"),
)


class SQLImpactSeedRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        if create_schema:
            metadata.create_all(engine)

    def put_mapping(
        self,
        scope: Scope,
        change: ChangedCodeReference,
        entity_ids: tuple[str, ...],
    ) -> None:
        if not entity_ids or any(not item for item in entity_ids):
            raise ValueError("topology identities are required")
        key = _scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(mapping_table).where(
                        mapping_table.c.scope == key,
                        mapping_table.c.change_kind == change.kind,
                        mapping_table.c.change_id == change.id,
                        mapping_table.c.revision == change.revision,
                    )
                )
                connection.execute(
                    mapping_table.insert(),
                    [
                        {
                            "scope": key,
                            "change_kind": change.kind,
                            "change_id": change.id,
                            "revision": change.revision,
                            "entity_id": entity_id,
                        }
                        for entity_id in sorted(set(entity_ids))
                    ],
                )

    def resolve(
        self, scope: Scope, changes: tuple[ChangedCodeReference, ...]
    ) -> tuple[str, ...]:
        if not changes:
            return ()
        key = _scope_key(scope)
        # Grouped by kind and revision, which a single change set almost always
        # shares, so this asks once rather than once per changed file.
        grouped: dict[tuple[str, str], set[str]] = {}
        for change in changes:
            grouped.setdefault((change.kind, change.revision), set()).add(change.id)

        found: set[str] = set()
        with self._engine.connect() as connection:
            for (kind, revision), identities in grouped.items():
                for row in rows_for(
                    identities,
                    lambda chunk, kind=kind, revision=revision: connection.execute(
                        select(mapping_table.c.entity_id).where(
                            mapping_table.c.scope == key,
                            mapping_table.c.change_kind == kind,
                            mapping_table.c.revision == revision,
                            mapping_table.c.change_id.in_(chunk),
                        )
                    ),
                ):
                    found.add(row[0])
        return tuple(sorted(found))


class SQLCompatibilityCheckRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        if create_schema:
            metadata.create_all(engine)

    def put_evidence(self, scope: Scope, evidence: CompatibilityCheck) -> None:
        key = _scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(check_table).where(
                        check_table.c.scope == key,
                        check_table.c.id == evidence.id,
                    )
                )
                connection.execute(
                    check_table.insert().values(
                        scope=key,
                        id=evidence.id,
                        provider_entity_id=evidence.provider_entity_id,
                        consumer_entity_id=evidence.consumer_entity_id,
                        status=evidence.status,
                        compatible=evidence.compatible,
                        before_revision=evidence.before_revision,
                        after_revision=evidence.after_revision,
                        summary=evidence.summary,
                        confidence=evidence.confidence,
                        stale=evidence.stale,
                        evidence=_encode_evidence(evidence.evidence),
                        properties=_encode(evidence.properties),
                    )
                )

    def read(self, query: CompatibilityCheckQuery) -> tuple[CompatibilityCheck, ...]:
        key = _scope_key(query.scope)
        wanted = sorted(query.entity_ids)
        statement = select(check_table).where(
            check_table.c.scope == key,
            check_table.c.provider_entity_id.in_(wanted),
            check_table.c.consumer_entity_id.in_(wanted),
            check_table.c.confidence >= query.minimum_confidence,
        )
        if query.statuses:
            statement = statement.where(
                check_table.c.status.in_(sorted(query.statuses))
            )
        if not query.include_stale:
            statement = statement.where(check_table.c.stale.is_(False))
        statement = statement.order_by(check_table.c.id).limit(query.limit)
        with self._engine.connect() as connection:
            return tuple(
                _evidence_from_row(row)
                for row in connection.execute(statement).mappings()
            )


def _evidence_from_row(row: Mapping[str, Any]) -> CompatibilityCheck:
    return CompatibilityCheck(
        id=row["id"],
        provider_entity_id=row["provider_entity_id"],
        consumer_entity_id=row["consumer_entity_id"],
        status=row["status"],
        compatible=row["compatible"],
        before_revision=row["before_revision"],
        after_revision=row["after_revision"],
        summary=row["summary"],
        confidence=row["confidence"],
        stale=bool(row["stale"]),
        evidence=tuple(
            TopologyEvidence(
                id=item["id"],
                kind=item["kind"],
                source=item["source"],
                revision=item.get("revision", ""),
                properties=item.get("properties", {}),
            )
            for item in json.loads(row["evidence"])
        ),
        properties=json.loads(row["properties"]),
    )


def _encode_evidence(items: tuple[TopologyEvidence, ...]) -> str:
    return json.dumps(
        [
            {
                "id": item.id,
                "kind": item.kind,
                "source": item.source,
                "revision": item.revision,
                "properties": dict(item.properties),
            }
            for item in items
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


def _scope_key(scope: Scope) -> str:
    return json.dumps(scope.values, separators=(",", ":"))


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
