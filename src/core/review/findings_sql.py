"""Findings, kept between reviews.

Written once and read back by state, so stored as read: one row, properties as
a blob, no columns nothing queries.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    Text,
    and_,
    func,
    select,
)

from src.core.scope import Scope

from .findings import FindingQuery, FindingResult, ReviewFinding

metadata = MetaData()

findings_table = Table(
    "review_findings",
    metadata,
    Column("scope", String(191), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("state", String(32), nullable=False, index=True),
    Column("summary", Text, nullable=False, default=""),
    Column("code_anchor", String(500), nullable=True),
    Column("properties", Text, nullable=False, default="{}"),
    # First said, and last still true. The second is what makes a finding
    # nobody has raised for months findable.
    Column("first_seen", DateTime(timezone=True), nullable=True),
    Column("last_seen", DateTime(timezone=True), nullable=True),
)


def _written(scope: Scope) -> str:
    """A scope as one string, since it is matched whole and never queried into."""
    return json.dumps(sorted(scope.values))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SQLFindingStore:
    """Findings, kept where the rest of what this machine knows is kept."""

    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        if create_schema:
            metadata.create_all(engine)

    def put_finding(self, scope: Scope, finding: ReviewFinding) -> None:
        """File one. A state already set survives, however often it is raised."""
        written = _written(scope)
        now = _now()
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    select(findings_table).where(
                        and_(
                            findings_table.c.scope == written,
                            findings_table.c.id == finding.id,
                        )
                    )
                )
                .mappings()
                .first()
            )

            if existing is not None:
                connection.execute(
                    findings_table.update()
                    .where(
                        and_(
                            findings_table.c.scope == written,
                            findings_table.c.id == finding.id,
                        )
                    )
                    .values(
                        # Not the state: only set_state changes that.
                        summary=finding.summary,
                        code_anchor=finding.code_anchor,
                        properties=json.dumps(dict(finding.properties)),
                        last_seen=now,
                    )
                )
                return

            connection.execute(
                findings_table.insert().values(
                    scope=written,
                    id=finding.id,
                    state=finding.state,
                    summary=finding.summary,
                    code_anchor=finding.code_anchor,
                    properties=json.dumps(dict(finding.properties)),
                    first_seen=now,
                    last_seen=now,
                )
            )

    def set_state(self, scope: Scope, identifier: str, state: str) -> bool:
        """Set a state. The only thing that does; filing never changes one."""
        if not state:
            raise ValueError("a finding needs a state")
        with self._engine.begin() as connection:
            done = connection.execute(
                findings_table.update()
                .where(
                    and_(
                        findings_table.c.scope == _written(scope),
                        findings_table.c.id == identifier,
                    )
                )
                .values(state=state, last_seen=_now())
            )
        return done.rowcount > 0

    def get_finding(self, scope: Scope, identifier: str) -> ReviewFinding | None:
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    select(findings_table).where(
                        and_(
                            findings_table.c.scope == _written(scope),
                            findings_table.c.id == identifier,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _read(row) if row else None

    def search(self, query: FindingQuery) -> FindingResult:
        where = [findings_table.c.scope == _written(query.scope)]
        if query.states:
            where.append(findings_table.c.state.in_(sorted(query.states)))

        asked = select(findings_table).where(and_(*where))
        # Properties are a blob, so filtering on one has to happen here and the
        # page with it. Without one the database can do both.
        if not query.properties:
            asked = asked.offset(query.offset).limit(query.limit)

        with self._engine.begin() as connection:
            rows = connection.execute(asked).mappings().all()
            counted = connection.execute(
                select(func.count()).select_from(findings_table).where(and_(*where))
            ).scalar_one()

        found = [_read(row) for row in rows]
        if not query.properties:
            return FindingResult(
                findings=tuple(found),
                total=counted,
                has_more=query.offset + len(found) < counted,
            )

        found = [
            finding
            for finding in found
            if all(
                finding.properties.get(key) == value
                for key, value in query.properties.items()
            )
        ]
        page = tuple(found[query.offset : query.offset + query.limit])
        return FindingResult(
            findings=page,
            total=len(found),
            has_more=query.offset + len(page) < len(found),
        )


def _read(row) -> ReviewFinding:
    return ReviewFinding(
        id=row["id"],
        state=row["state"],
        summary=row["summary"] or "",
        code_anchor=row["code_anchor"],
        properties=json.loads(row["properties"] or "{}"),
    )
