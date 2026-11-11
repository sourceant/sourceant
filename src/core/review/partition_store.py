import json
from datetime import datetime, timedelta
from secrets import token_hex

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
runs = Table(
    "review_partition_runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("workspace", String(255), nullable=False),
    Column("repository", String(500), nullable=False),
    Column("revision", String(255), nullable=False),
    Column("plan", Text, nullable=False),
    Column("maximum_calls", Integer, nullable=False),
    Column("calls", Integer, nullable=False),
    Column("state", String(32), nullable=False),
)
parts = Table(
    "review_partition_parts",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("state", String(32), nullable=False),
    Column("lease", String(64), nullable=False),
    Column("leased_until", DateTime, nullable=True),
    Column("answer", Text, nullable=True),
)

responses = Table(
    "review_partition_responses",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("id", String(64), primary_key=True),
    Column("answer", Text, nullable=False),
)


class SQLReviewWork:
    def __init__(self, engine, *, create_schema=False):
        self.engine = engine
        if create_schema:
            metadata.create_all(engine)

    def open(self, identifier, *, workspace, repository, revision, plan, maximum_calls):
        with self.engine.begin() as connection:
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(runs).values(
                            id=identifier,
                            workspace=workspace,
                            repository=repository,
                            revision=revision,
                            plan=json.dumps(plan, sort_keys=True),
                            maximum_calls=maximum_calls,
                            calls=0,
                            state="running",
                        )
                    )
                    for part in plan["parts"]:
                        connection.execute(
                            insert(parts).values(
                                run_id=identifier,
                                id=part["id"],
                                state="queued",
                                lease="",
                            )
                        )
            except IntegrityError:
                row = (
                    connection.execute(select(runs).where(runs.c.id == identifier))
                    .mappings()
                    .one()
                )
                if (
                    row["workspace"],
                    row["repository"],
                    row["revision"],
                    row["plan"],
                ) != (
                    workspace,
                    repository,
                    revision,
                    json.dumps(plan, sort_keys=True),
                ):
                    raise ValueError("A persisted review plan cannot change identity")

    def reserve_call(self, identifier):
        with self.engine.begin() as connection:
            result = connection.execute(
                update(runs)
                .where(
                    runs.c.id == identifier,
                    runs.c.state == "running",
                    runs.c.calls < runs.c.maximum_calls,
                )
                .values(calls=runs.c.calls + 1)
            )
            if result.rowcount != 1:
                raise RuntimeError("The review has no remaining model-call allowance")

    def claim(self, identifier, part_id, *, seconds=900):
        lease = token_hex(24)
        with self.engine.begin() as connection:
            active = (
                select(runs.c.id)
                .where(runs.c.id == identifier, runs.c.state == "running")
                .exists()
            )
            result = connection.execute(
                update(parts)
                .where(
                    parts.c.run_id == identifier,
                    parts.c.id == part_id,
                    active,
                    or_(
                        parts.c.state == "queued",
                        and_(
                            parts.c.state == "running",
                            parts.c.leased_until < datetime.utcnow(),
                        ),
                    ),
                )
                .values(
                    state="running",
                    lease=lease,
                    leased_until=datetime.utcnow() + timedelta(seconds=seconds),
                )
            )
            return lease if result.rowcount == 1 else None

    def finish(self, identifier, part_id, lease, answer):
        with self.engine.begin() as connection:
            result = connection.execute(
                update(parts)
                .where(
                    parts.c.run_id == identifier,
                    parts.c.id == part_id,
                    select(runs.c.id)
                    .where(runs.c.id == identifier, runs.c.state == "running")
                    .exists(),
                    parts.c.lease == lease,
                    parts.c.state == "running",
                )
                .values(state="done", answer=json.dumps(answer), leased_until=None)
            )
            return result.rowcount == 1

    def answers(self, identifier):
        with self.engine.connect() as connection:
            return {
                row["id"]: json.loads(row["answer"])
                for row in connection.execute(
                    select(parts).where(
                        parts.c.run_id == identifier, parts.c.state == "done"
                    )
                ).mappings()
            }

    def cancel(self, identifier):
        with self.engine.begin() as connection:
            connection.execute(
                update(runs).where(runs.c.id == identifier).values(state="cancelled")
            )

    def response(self, identifier, key):
        self.ensure_active(identifier)
        with self.engine.connect() as connection:
            return connection.execute(
                select(responses.c.answer).where(
                    responses.c.run_id == identifier, responses.c.id == key
                )
            ).scalar_one_or_none()

    def save_response(self, identifier, key, answer):
        with self.engine.begin() as connection:
            active = connection.execute(
                select(runs.c.state).where(runs.c.id == identifier)
            ).scalar_one()
            if active != "running":
                return False
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(responses).values(
                            run_id=identifier, id=key, answer=answer
                        )
                    )
            except IntegrityError:
                pass
            return True

    def progress(self, identifier):
        with self.engine.connect() as connection:
            run = (
                connection.execute(select(runs).where(runs.c.id == identifier))
                .mappings()
                .one_or_none()
            )
            if run is None:
                return None
            states = list(
                connection.execute(
                    select(parts.c.state).where(parts.c.run_id == identifier)
                ).scalars()
            )
            return {
                "run_id": identifier,
                "state": run["state"],
                "model_calls": run["calls"],
                "maximum_model_calls": run["maximum_calls"],
                "chunks": {
                    state: states.count(state)
                    for state in ("queued", "running", "done", "failed")
                },
            }

    def release(self, identifier, part_id, lease):
        with self.engine.begin() as connection:
            connection.execute(
                update(parts)
                .where(
                    parts.c.run_id == identifier,
                    parts.c.id == part_id,
                    parts.c.state == "running",
                    parts.c.lease == lease,
                )
                .values(state="queued", lease="", leased_until=None)
            )

    def ensure_active(self, identifier):
        with self.engine.connect() as connection:
            state = connection.execute(
                select(runs.c.state).where(runs.c.id == identifier)
            ).scalar_one_or_none()
        if state != "running":
            raise RuntimeError("The review run is no longer active")

    def fail(self, identifier):
        with self.engine.begin() as connection:
            result = connection.execute(
                update(runs)
                .where(runs.c.id == identifier, runs.c.state == "running")
                .values(state="failed")
            )
            if result.rowcount == 1:
                connection.execute(
                    update(parts)
                    .where(
                        parts.c.run_id == identifier,
                        parts.c.state.in_(("queued", "running")),
                    )
                    .values(state="failed", leased_until=None)
                )

    def complete(self, identifier):
        with self.engine.begin() as connection:
            result = connection.execute(
                update(runs)
                .where(runs.c.id == identifier, runs.c.state == "running")
                .values(state="done")
            )
            if result.rowcount == 1:
                connection.execute(
                    update(parts)
                    .where(
                        parts.c.run_id == identifier,
                        parts.c.state.in_(("queued", "running")),
                    )
                    .values(
                        state="done",
                        answer=json.dumps({"review": None, "coverage": []}),
                        leased_until=None,
                    )
                )
            return result.rowcount == 1
