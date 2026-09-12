from __future__ import annotations

import json
from threading import RLock
from typing import Any, Mapping

from sqlalchemy import (
    BigInteger,
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

from src.core import scopes
from src.core.scope import Scope
from src.core.sql_support import rows_for
from src.core.topology import TopologyEvidence

from .models import (
    ChangedCodeReference,
    CompatibilityCheck,
    CompatibilityCheckQuery,
)

metadata = MetaData()

# The identity hashed rather than spelled out, because a kind, a repository
# and a path together exceed the 3072 bytes MySQL allows in a key. The parts
# are kept beside it so a row still says what it is about.
mapping_table = Table(
    "impact_code_mappings",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("reference_key", String(64), primary_key=True),
    Column("entity_id", String(255), primary_key=True),
    Column("change_kind", String(64), nullable=False),
    Column("repository", String(255), nullable=False),
    Column("change_path", String(383), nullable=False),
    # Kept as provenance, never as identity: it says when the mapping was
    # written, not which reviews it answers.
    Column("revision", String(64), nullable=False),
)

check_table = Table(
    "compatibility_checks",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
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
    Index("ix_compatibility_checks_scope_provider", "scope_id", "provider_entity_id"),
    Index("ix_compatibility_checks_scope_consumer", "scope_id", "consumer_entity_id"),
)


class SQLImpactSeedRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        if create_schema:
            scopes.ensure(engine)
            metadata.create_all(engine)

    def put_mapping(
        self,
        scope: Scope,
        change: ChangedCodeReference,
        entity_ids: tuple[str, ...],
    ) -> None:
        if not entity_ids or any(not item for item in entity_ids):
            raise ValueError("topology identities are required")
        kind, repository, path = change.identity
        with self._lock:
            with self._engine.begin() as connection:
                key = scopes.remembered(connection, scope)
                connection.execute(
                    delete(mapping_table).where(
                        mapping_table.c.scope_id == key,
                        mapping_table.c.reference_key == change.key,
                    )
                )
                connection.execute(
                    mapping_table.insert(),
                    [
                        {
                            "scope_id": key,
                            "reference_key": change.key,
                            "entity_id": entity_id,
                            "change_kind": kind,
                            "repository": repository,
                            "change_path": path,
                            "revision": change.revision,
                        }
                        for entity_id in sorted(set(entity_ids))
                    ],
                )

    def resolve(
        self, scope: Scope, changes: tuple[ChangedCodeReference, ...]
    ) -> tuple[str, ...]:
        if not changes:
            return ()
        key = scopes.known_id(self._engine, scope)
        # One question for the whole change set. The identity carries the kind
        # and the repository already, so nothing has to be grouped by them.
        wanted = {change.key for change in changes}

        found: set[str] = set()
        with self._engine.connect() as connection:
            for row in rows_for(
                wanted,
                lambda chunk: connection.execute(
                    select(mapping_table.c.entity_id).where(
                        mapping_table.c.scope_id == key,
                        mapping_table.c.reference_key.in_(chunk),
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
            scopes.ensure(engine)
            metadata.create_all(engine)

    def put_evidence(self, scope: Scope, evidence: CompatibilityCheck) -> None:
        with self._lock:
            with self._engine.begin() as connection:
                key = scopes.remembered(connection, scope)
                connection.execute(
                    delete(check_table).where(
                        check_table.c.scope_id == key,
                        check_table.c.id == evidence.id,
                    )
                )
                connection.execute(
                    check_table.insert().values(
                        scope_id=key,
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
        key = scopes.known_id(self._engine, query.scope)
        wanted = sorted(query.entity_ids)
        statement = select(check_table).where(
            check_table.c.scope_id == key,
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


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
