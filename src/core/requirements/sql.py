from __future__ import annotations

import json
from threading import RLock
from typing import Any, Mapping

from sqlalchemy import (
    BigInteger,
    Column,
    Engine,
    Index,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    select,
)

from src.core import scopes
from src.core.scope import Scope
from src.core.sql_support import rows_for

from .models import (
    CODE,
    TEST,
    CoverageQuery,
    CoverageReport,
    Requirement,
    RequirementCoverage,
    RequirementLink,
    RequirementQuery,
    RequirementResult,
)

metadata = MetaData()

requirement_table = Table(
    "requirements",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("kind", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("summary", Text, nullable=False),
    Column("priority", String(255), nullable=False, default=""),
    Index("ix_requirements_scope_priority", "scope_id", "priority"),
    Column("external_ref", String(500), nullable=False),
    Column("properties", Text, nullable=False),
    Index("ix_requirements_scope_external_ref", "scope_id", "external_ref"),
)

link_table = Table(
    "requirement_links",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("requirement_id", String(255), nullable=False),
    Column("target_kind", String(64), nullable=False),
    Column("target_id", String(500), nullable=False),
    Column("properties", Text, nullable=False),
    Index("ix_requirement_links_scope_requirement", "scope_id", "requirement_id"),
    Index("ix_requirement_links_scope_target", "scope_id", "target_id"),
)


class SQLRequirementsRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        if create_schema:
            scopes.ensure(engine)
            metadata.create_all(engine)

    def put(self, scope: Scope, requirement: Requirement) -> None:
        with self._lock:
            with self._engine.begin() as connection:
                key = scopes.remembered(connection, scope)
                connection.execute(
                    delete(requirement_table).where(
                        requirement_table.c.scope_id == key,
                        requirement_table.c.id == requirement.id,
                    )
                )
                connection.execute(
                    requirement_table.insert().values(
                        scope_id=key,
                        id=requirement.id,
                        kind=requirement.kind,
                        status=requirement.status,
                        summary=requirement.summary,
                        priority=requirement.priority,
                        external_ref=requirement.external_ref,
                        properties=_encode(requirement.properties),
                    )
                )

    def put_link(self, scope: Scope, link: RequirementLink) -> None:
        with self._lock:
            with self._engine.begin() as connection:
                key = scopes.remembered(connection, scope)
                known = connection.execute(
                    select(requirement_table.c.id).where(
                        requirement_table.c.scope_id == key,
                        requirement_table.c.id == link.requirement_id,
                    )
                ).first()
                if known is None:
                    raise ValueError("a link needs a requirement in the same scope")
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope_id == key, link_table.c.id == link.id
                    )
                )
                connection.execute(
                    link_table.insert().values(
                        scope_id=key,
                        id=link.id,
                        requirement_id=link.requirement_id,
                        target_kind=link.target_kind,
                        target_id=link.target_id,
                        properties=_encode(link.properties),
                    )
                )

    def remove_link(self, scope: Scope, link_id: str) -> bool:
        key = scopes.known_id(self._engine, scope)
        with self._lock, self._engine.begin() as connection:
            result = connection.execute(
                delete(link_table).where(
                    link_table.c.scope_id == key, link_table.c.id == link_id
                )
            )
            return result.rowcount > 0

    def remove(self, scope: Scope, requirement_id: str) -> None:
        key = scopes.known_id(self._engine, scope)
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope_id == key,
                        link_table.c.requirement_id == requirement_id,
                    )
                )
                connection.execute(
                    delete(requirement_table).where(
                        requirement_table.c.scope_id == key,
                        requirement_table.c.id == requirement_id,
                    )
                )

    def search(self, query: RequirementQuery) -> RequirementResult:
        key = scopes.known_id(self._engine, query.scope)
        statement = select(requirement_table).where(requirement_table.c.scope_id == key)
        if query.ids:
            statement = statement.where(
                requirement_table.c.id.in_(sorted(query.ids)),
            )
        if query.kinds:
            statement = statement.where(
                requirement_table.c.kind.in_(sorted(query.kinds)),
            )
        if query.statuses:
            statement = statement.where(
                requirement_table.c.status.in_(sorted(query.statuses)),
            )
        if query.priorities:
            statement = statement.where(
                requirement_table.c.priority.in_(sorted(query.priorities))
            )
        if query.external_refs:
            statement = statement.where(
                requirement_table.c.external_ref.in_(sorted(query.external_refs)),
            )
        with self._engine.connect() as connection:
            total = connection.execute(
                select(func.count()).select_from(statement.subquery())
            ).scalar_one()
            rows = list(
                connection.execute(
                    statement.order_by(requirement_table.c.id)
                    .limit(query.limit)
                    .offset(query.offset)
                ).mappings()
            )
        items = tuple(_requirement_from_row(row) for row in rows)
        return RequirementResult(
            items=items,
            total=total,
            has_more=query.offset + len(items) < total,
        )

    def get_links(
        self, scope: Scope, requirement_ids: frozenset[str]
    ) -> tuple[RequirementLink, ...]:
        key = scopes.known_id(self._engine, scope)
        with self._engine.connect() as connection:
            if not requirement_ids:
                rows = list(
                    connection.execute(
                        select(link_table)
                        .where(link_table.c.scope_id == key)
                        .order_by(link_table.c.id)
                    ).mappings()
                )
            else:
                rows = rows_for(
                    requirement_ids,
                    lambda chunk: connection.execute(
                        select(link_table).where(
                            link_table.c.scope_id == key,
                            link_table.c.requirement_id.in_(chunk),
                        )
                    ).mappings(),
                )
        found = {row["id"]: _link_from_row(row) for row in rows}
        return tuple(found[key] for key in sorted(found))

    def coverage(self, query: CoverageQuery) -> CoverageReport:
        key = scopes.known_id(self._engine, query.scope)
        requirement_ids = set(query.requirement_ids)
        if query.paths:
            with self._engine.connect() as connection:
                for row in rows_for(
                    query.paths,
                    lambda chunk: connection.execute(
                        select(link_table.c.requirement_id).where(
                            link_table.c.scope_id == key,
                            link_table.c.target_id.in_(chunk),
                        )
                    ),
                ):
                    requirement_ids.add(row[0])
            if not requirement_ids:
                return CoverageReport(items=(), truncated=False)

        if requirement_ids:
            with self._engine.connect() as connection:
                rows = rows_for(
                    requirement_ids,
                    lambda chunk: connection.execute(
                        select(requirement_table).where(
                            requirement_table.c.scope_id == key,
                            requirement_table.c.id.in_(chunk),
                        )
                    ).mappings(),
                )
            matching = tuple(
                _requirement_from_row(row)
                for row in sorted(rows, key=lambda row: row["id"])
            )
            requirements = matching[: query.limit]
            truncated = len(matching) > query.limit
        else:
            found = self.search(RequirementQuery(scope=query.scope, limit=query.limit))
            requirements = found.items
            truncated = found.has_more

        links = self.get_links(query.scope, frozenset(item.id for item in requirements))
        grouped: dict[str, list[RequirementLink]] = {}
        for link in links:
            grouped.setdefault(link.requirement_id, []).append(link)

        items = []
        for requirement in requirements:
            related = grouped.get(requirement.id, [])
            items.append(
                RequirementCoverage(
                    requirement_id=requirement.id,
                    status=requirement.status,
                    code_links=sum(1 for link in related if link.target_kind == CODE),
                    test_links=sum(1 for link in related if link.target_kind == TEST),
                    paths=tuple(
                        sorted(
                            {
                                link.target_id
                                for link in related
                                if link.target_kind in (CODE, TEST)
                            }
                        )
                    ),
                )
            )
        return CoverageReport(items=tuple(items), truncated=truncated)


def _requirement_from_row(row: Mapping[str, Any]) -> Requirement:
    return Requirement(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        summary=row["summary"],
        priority=row["priority"],
        external_ref=row["external_ref"],
        properties=json.loads(row["properties"]),
    )


def _link_from_row(row: Mapping[str, Any]) -> RequirementLink:
    return RequirementLink(
        id=row["id"],
        requirement_id=row["requirement_id"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        properties=json.loads(row["properties"]),
    )


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
