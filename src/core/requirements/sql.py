from __future__ import annotations

import json
from threading import RLock
from typing import Any, Mapping

from sqlalchemy import (
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

from src.core.scope import Scope

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
scope_key_type = String(500)

requirement_table = Table(
    "requirements",
    metadata,
    Column("scope", scope_key_type, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("kind", String(255), nullable=False),
    Column("status", String(255), nullable=False),
    Column("summary", Text, nullable=False),
    Column("external_ref", String(500), nullable=False),
    Column("properties", Text, nullable=False),
    Index("ix_requirements_scope_external_ref", "scope", "external_ref"),
)

link_table = Table(
    "requirement_links",
    metadata,
    Column("scope", scope_key_type, primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("requirement_id", String(255), nullable=False),
    Column("target_kind", String(64), nullable=False),
    Column("target_id", String(500), nullable=False),
    Column("properties", Text, nullable=False),
    Index("ix_requirement_links_scope_requirement", "scope", "requirement_id"),
    Index("ix_requirement_links_scope_target", "scope", "target_id"),
)


class SQLRequirementsRepository:
    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        self._lock = RLock()
        if create_schema:
            metadata.create_all(engine)

    def put(self, scope: Scope, requirement: Requirement) -> None:
        key = _scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(requirement_table).where(
                        requirement_table.c.scope == key,
                        requirement_table.c.id == requirement.id,
                    )
                )
                connection.execute(
                    requirement_table.insert().values(
                        scope=key,
                        id=requirement.id,
                        kind=requirement.kind,
                        status=requirement.status,
                        summary=requirement.summary,
                        external_ref=requirement.external_ref,
                        properties=_encode(requirement.properties),
                    )
                )

    def put_link(self, scope: Scope, link: RequirementLink) -> None:
        key = _scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                known = connection.execute(
                    select(requirement_table.c.id).where(
                        requirement_table.c.scope == key,
                        requirement_table.c.id == link.requirement_id,
                    )
                ).first()
                if known is None:
                    raise ValueError("a link needs a requirement in the same scope")
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope == key, link_table.c.id == link.id
                    )
                )
                connection.execute(
                    link_table.insert().values(
                        scope=key,
                        id=link.id,
                        requirement_id=link.requirement_id,
                        target_kind=link.target_kind,
                        target_id=link.target_id,
                        properties=_encode(link.properties),
                    )
                )

    def remove(self, scope: Scope, requirement_id: str) -> None:
        key = _scope_key(scope)
        with self._lock:
            with self._engine.begin() as connection:
                connection.execute(
                    delete(link_table).where(
                        link_table.c.scope == key,
                        link_table.c.requirement_id == requirement_id,
                    )
                )
                connection.execute(
                    delete(requirement_table).where(
                        requirement_table.c.scope == key,
                        requirement_table.c.id == requirement_id,
                    )
                )

    def search(self, query: RequirementQuery) -> RequirementResult:
        key = _scope_key(query.scope)
        statement = select(requirement_table).where(requirement_table.c.scope == key)
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
        key = _scope_key(scope)
        statement = select(link_table).where(link_table.c.scope == key)
        if requirement_ids:
            statement = statement.where(
                link_table.c.requirement_id.in_(sorted(requirement_ids)),
            )
        statement = statement.order_by(link_table.c.id)
        with self._engine.connect() as connection:
            return tuple(
                _link_from_row(row) for row in connection.execute(statement).mappings()
            )

    def coverage(self, query: CoverageQuery) -> CoverageReport:
        key = _scope_key(query.scope)
        requirement_ids = set(query.requirement_ids)
        if query.paths:
            with self._engine.connect() as connection:
                for row in connection.execute(
                    select(link_table.c.requirement_id).where(
                        link_table.c.scope == key,
                        link_table.c.target_id.in_(sorted(query.paths)),
                    )
                ):
                    requirement_ids.add(row[0])
            if not requirement_ids:
                return CoverageReport(items=())

        found = self.search(
            RequirementQuery(
                scope=query.scope,
                ids=frozenset(requirement_ids),
                limit=100,
            )
        )
        links = self.get_links(query.scope, frozenset(item.id for item in found.items))
        grouped: dict[str, list[RequirementLink]] = {}
        for link in links:
            grouped.setdefault(link.requirement_id, []).append(link)

        items = []
        for requirement in found.items:
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
        return CoverageReport(items=tuple(items))


def _requirement_from_row(row: Mapping[str, Any]) -> Requirement:
    return Requirement(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        summary=row["summary"],
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


def _scope_key(scope: Scope) -> str:
    return json.dumps(scope.values, separators=(",", ":"))


def _encode(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
