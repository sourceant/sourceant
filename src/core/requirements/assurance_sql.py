from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    JSON,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
)

from src.core import scopes
from .assurance import requirement_revision
from .sql import requirement_table, _requirement_from_row

metadata = MetaData()


def table(name, *columns):
    return Table(
        name,
        metadata,
        Column("scope_id", BigInteger, primary_key=True),
        Column("content", JSON, nullable=False),
        Column("recorded_at", String(40), nullable=False),
        Column("recorded_by", String(255), nullable=False),
        Column("deleted_at", DateTime(timezone=True), nullable=True),
        *columns,
    )


baselines = table(
    "requirement_baselines",
    Column("requirement_id", String(255), primary_key=True),
    Column("revision", String(64), primary_key=True),
)
criteria = table(
    "requirement_criteria",
    Column("requirement_id", String(255), primary_key=True),
    Column("id", String(255), primary_key=True),
    Column("revision", String(64), primary_key=True),
    Column("previous_revision", String(64), nullable=False),
    UniqueConstraint(
        "scope_id",
        "requirement_id",
        "id",
        "previous_revision",
        name="uq_criterion_previous",
    ),
    Index("ix_criterion_requirement", "scope_id", "requirement_id", "recorded_at"),
)
evidence_records = table(
    "requirement_evidence",
    Column("id", String(255), primary_key=True),
    Index("ix_evidence_recorded", "scope_id", "recorded_at", "id"),
)
assessments = table(
    "requirement_assessments",
    Column("id", String(255), primary_key=True),
    Column("requirement_id", String(255), nullable=False),
    Column("criterion_id", String(255), nullable=False),
    Column("supersedes", String(255), nullable=False),
    UniqueConstraint(
        "scope_id",
        "requirement_id",
        "criterion_id",
        "supersedes",
        name="uq_assessment_previous",
    ),
    Index(
        "ix_assessment_criterion",
        "scope_id",
        "requirement_id",
        "criterion_id",
        "recorded_at",
    ),
)
assessment_evidence = Table(
    "requirement_assessment_evidence",
    metadata,
    Column("scope_id", BigInteger, primary_key=True),
    Column("assessment_id", String(255), primary_key=True),
    Column("evidence_id", String(255), primary_key=True),
    Index("ix_evidence_assessments", "scope_id", "evidence_id"),
)


def unpack(row):
    return {
        **row["content"],
        "recorded_at": row["recorded_at"],
        "recorded_by": row["recorded_by"],
    }


class SQLAssuranceRepository:
    def __init__(self, engine, create_schema=False):
        self.engine = engine
        if create_schema:
            scopes.ensure(engine)
            metadata.create_all(engine)

    def _requirement(self, connection, key, identifier, lock=True):
        query = select(requirement_table).where(
            requirement_table.c.scope_id == key, requirement_table.c.id == identifier
        )
        row = (
            connection.execute(query.with_for_update() if lock else query)
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("Requirement not found in this scope")
        return _requirement_from_row(row)

    def _current(self, connection, table, key, *conditions):
        successor = table.alias("successor")
        identity = "id" if table is criteria else "criterion_id"
        predecessor = "previous_revision" if table is criteria else "supersedes"
        version = "revision" if table is criteria else "id"
        has_successor = (
            select(successor.c.id)
            .where(
                successor.c.scope_id == table.c.scope_id,
                successor.c.requirement_id == table.c.requirement_id,
                successor.c[identity] == table.c[identity],
                successor.c[predecessor] == table.c[version],
            )
            .exists()
        )
        return list(
            connection.execute(
                select(table)
                .where(
                    table.c.scope_id == key,
                    table.c.deleted_at.is_(None),
                    ~has_successor,
                    *conditions,
                )
                .order_by(table.c.id)
            ).mappings()
        )

    def history(self, scope, requirement_id, limit=50, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Use a limit of 1 to 100 and a nonnegative offset")
        key = scopes.known_id(self.engine, scope)
        with self.engine.connect() as connection:
            self._requirement(connection, key, requirement_id, lock=False)
            result = {}
            for name, source in (("criteria", criteria), ("assessments", assessments)):
                query = select(source).where(
                    source.c.scope_id == key,
                    source.c.requirement_id == requirement_id,
                    source.c.deleted_at.is_(None),
                )
                total = connection.execute(
                    select(func.count()).select_from(query.subquery())
                ).scalar_one()
                rows = connection.execute(
                    query.order_by(source.c.recorded_at.desc(), source.c.id)
                    .limit(limit)
                    .offset(offset)
                ).mappings()
                result[name] = {
                    "items": [unpack(row) for row in rows],
                    "total": total,
                    "has_more": offset + limit < total,
                }
            revisions = {
                item["requirement_revision"]
                for page in result.values()
                for item in page["items"]
            }
            result["baselines"] = [
                {"revision": row["revision"], "requirement": unpack(row)}
                for row in connection.execute(
                    select(baselines).where(
                        baselines.c.scope_id == key,
                        baselines.c.requirement_id == requirement_id,
                        baselines.c.revision.in_(revisions),
                    )
                ).mappings()
            ]
            return result

    def _insert(self, connection, table, key, content, actor, **columns):
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        connection.execute(
            table.insert().values(
                scope_id=key,
                content=content,
                recorded_at=now,
                recorded_by=actor,
                **columns,
            )
        )
        return {**content, "recorded_at": now, "recorded_by": actor}

    def _baseline(self, connection, key, requirement, actor):
        revision = requirement_revision(requirement)
        if not connection.execute(
            select(baselines.c.revision).where(
                baselines.c.scope_id == key,
                baselines.c.requirement_id == requirement.id,
                baselines.c.revision == revision,
            )
        ).first():
            self._insert(
                connection,
                baselines,
                key,
                asdict(requirement),
                actor,
                requirement_id=requirement.id,
                revision=revision,
            )
        return revision

    def put_criterion(self, scope, criterion, expected_revision, actor):
        with self.engine.begin() as connection:
            key = scopes.remembered(connection, scope)
            requirement = self._requirement(connection, key, criterion.requirement_id)
            known_scenarios = {
                item["id"]
                for item in requirement.properties.get("behavior", {}).get(
                    "scenarios", []
                )
            }
            if set(criterion.scenario_ids) - known_scenarios:
                raise ValueError("A scenario does not belong to this requirement")
            history = self._current(
                connection,
                criteria,
                key,
                criteria.c.requirement_id == criterion.requirement_id,
                criteria.c.id == criterion.id,
            )
            previous = history[-1]["revision"] if history else ""
            if previous != expected_revision:
                raise ValueError("The criterion changed; refresh before editing")
            revision = uuid4().hex
            baseline = self._baseline(connection, key, requirement, actor)
            content = {
                **asdict(criterion),
                "revision": revision,
                "requirement_revision": baseline,
                "previous_revision": previous,
            }
            return self._insert(
                connection,
                criteria,
                key,
                content,
                actor,
                requirement_id=criterion.requirement_id,
                id=criterion.id,
                revision=revision,
                previous_revision=previous,
            )

    def record_evidence(self, scope, evidence, actor):
        with self.engine.begin() as connection:
            key = scopes.remembered(connection, scope)
            if connection.execute(
                select(evidence_records.c.id).where(
                    evidence_records.c.scope_id == key,
                    evidence_records.c.id == evidence.id,
                )
            ).first():
                raise ValueError(
                    "Evidence is immutable; use a new id for a new observation"
                )
            return self._insert(
                connection,
                evidence_records,
                key,
                asdict(evidence),
                actor,
                id=evidence.id,
            )

    def assess(self, scope, assessment, actor):
        with self.engine.begin() as connection:
            key = scopes.remembered(connection, scope)
            requirement = self._requirement(connection, key, assessment.requirement_id)
            history = self._current(
                connection,
                criteria,
                key,
                criteria.c.requirement_id == assessment.requirement_id,
                criteria.c.id == assessment.criterion_id,
            )
            if not history or not history[-1]["content"]["active"]:
                raise ValueError("An active acceptance criterion is required")
            criterion = history[-1]["content"]
            revision = requirement_revision(requirement)
            if (
                revision != assessment.requirement_revision
                or criterion["revision"] != assessment.criterion_revision
                or criterion["requirement_revision"] != revision
            ):
                raise ValueError(
                    "Requirement or criterion changed; revise the criterion before assessing it"
                )
            records = (
                connection.execute(
                    select(evidence_records.c.id).where(
                        evidence_records.c.scope_id == key,
                        evidence_records.c.id.in_(assessment.evidence_ids),
                        evidence_records.c.deleted_at.is_(None),
                    )
                )
                .scalars()
                .all()
            )
            if set(records) != set(assessment.evidence_ids):
                raise ValueError("Every evidence record must exist in this scope")
            previous = self._current(
                connection,
                assessments,
                key,
                assessments.c.requirement_id == assessment.requirement_id,
                assessments.c.criterion_id == assessment.criterion_id,
            )
            if assessment.supersedes != (previous[-1]["id"] if previous else ""):
                raise ValueError(
                    "Assessment history changed; refresh before recording a decision"
                )
            self._baseline(connection, key, requirement, actor)
            result = self._insert(
                connection,
                assessments,
                key,
                asdict(assessment),
                actor,
                id=assessment.id,
                requirement_id=assessment.requirement_id,
                criterion_id=assessment.criterion_id,
                supersedes=assessment.supersedes,
            )
            connection.execute(
                assessment_evidence.insert(),
                [
                    {
                        "scope_id": key,
                        "assessment_id": assessment.id,
                        "evidence_id": identifier,
                    }
                    for identifier in assessment.evidence_ids
                ],
            )
            return result

    def get_evidence(self, scope, evidence_id):
        key = scopes.known_id(self.engine, scope)
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(evidence_records).where(
                        evidence_records.c.scope_id == key,
                        evidence_records.c.id == evidence_id,
                        evidence_records.c.deleted_at.is_(None),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ValueError("Evidence not found in this scope")
            return unpack(row)

    def evidence(self, scope, limit=50, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Use a limit of 1 to 100 and a nonnegative offset")
        key = scopes.known_id(self.engine, scope)
        with self.engine.connect() as connection:
            query = select(evidence_records).where(
                evidence_records.c.scope_id == key,
                evidence_records.c.deleted_at.is_(None),
            )
            total = connection.execute(
                select(func.count()).select_from(query.subquery())
            ).scalar_one()
            rows = connection.execute(
                query.order_by(
                    evidence_records.c.recorded_at.desc(), evidence_records.c.id
                )
                .limit(limit)
                .offset(offset)
            ).mappings()
            return {
                "items": [unpack(row) for row in rows],
                "total": total,
                "has_more": offset + limit < total,
            }

    def report(self, scope, requirement_id):
        key = scopes.known_id(self.engine, scope)
        with self.engine.connect() as connection:
            requirement = self._requirement(connection, key, requirement_id, lock=False)
            revision = requirement_revision(requirement)
            history = self._current(
                connection, criteria, key, criteria.c.requirement_id == requirement_id
            )
            decisions = self._current(
                connection,
                assessments,
                key,
                assessments.c.requirement_id == requirement_id,
            )
            latest_criteria = {row["id"]: unpack(row) for row in history}
            latest_decisions = {row["criterion_id"]: unpack(row) for row in decisions}
            items = []
            for criterion in latest_criteria.values():
                decision = latest_decisions.get(criterion["id"])
                stale = criterion["requirement_revision"] != revision or bool(
                    decision
                    and (
                        decision["requirement_revision"] != revision
                        or decision["criterion_revision"] != criterion["revision"]
                    )
                )
                state = (
                    "retired"
                    if not criterion["active"]
                    else (
                        "needs_review"
                        if stale
                        else decision["outcome"] if decision else "unassessed"
                    )
                )
                items.append({**criterion, "state": state, "assessment": decision})
            active = [item for item in items if item["active"]]
            states = {item["state"] for item in active}
            state = (
                "undefined"
                if not active
                else (
                    "failed"
                    if "failed" in states
                    else (
                        "needs_review"
                        if "needs_review" in states
                        else (
                            "unassessed"
                            if "unassessed" in states
                            else (
                                "inconclusive"
                                if "inconclusive" in states
                                else "waived" if "waived" in states else "passed"
                            )
                        )
                    )
                )
            )
            return {
                "requirement_id": requirement_id,
                "revision": revision,
                "state": state,
                "criteria": items,
            }
