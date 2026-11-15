import json

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    JSON,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from src.core.execution_attempts import (
    ExecutionAttempt,
    SQLExecutionAttempts,
    metadata as execution_metadata,
)

metadata = MetaData()
runs = Table(
    "review_runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("workspace", String(255), nullable=False),
    Column("repository", String(500), nullable=False),
    Column("revision", String(255), nullable=False),
    Column("plan", JSON, nullable=False),
    Column("maximum_calls", Integer, nullable=False),
    Column("calls", Integer, nullable=False),
    Column("state", String(32), nullable=False),
)
chunk_table = Table(
    "review_chunks",
    metadata,
    Column("review_id", String(64), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("result", JSON, nullable=True),
    Column("group", String(500), nullable=False),
    Column("files", JSON, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("diff", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False),
)

responses = Table(
    "review_responses",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("id", String(64), primary_key=True),
    Column("answer", JSON, nullable=False),
)


class SQLReviewWork:
    def __init__(self, engine, *, create_schema=False):
        self.engine = engine
        self.executions = SQLExecutionAttempts()
        if create_schema:
            metadata.create_all(engine)
            execution_metadata.create_all(engine)

    def open(
        self,
        identifier,
        *,
        workspace,
        repository,
        revision,
        plan,
        maximum_calls,
        chunks=(),
    ):
        from .chunks import group_for

        planned = {chunk.id: chunk for chunk in chunks}
        plan = json.loads(json.dumps(plan))
        if planned:
            if any(chunk.review_id != identifier for chunk in planned.values()):
                raise ValueError("Planned chunks must belong to this review")
            definitions = json.loads(
                json.dumps([chunk.definition() for chunk in chunks])
            )
            if "parts" in plan and plan["parts"] != definitions:
                raise ValueError("Planned chunks must match the review plan")
            plan = {**plan, "parts": definitions}
        with self.engine.begin() as connection:
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(runs).values(
                            id=identifier,
                            workspace=workspace,
                            repository=repository,
                            revision=revision,
                            plan=plan,
                            maximum_calls=maximum_calls,
                            calls=0,
                            state="running",
                        )
                    )
                    for part in plan["parts"]:
                        connection.execute(
                            insert(chunk_table).values(
                                review_id=identifier,
                                id=part["id"],
                                status="pending",
                                group=(
                                    planned[part["id"]].group
                                    if part["id"] in planned
                                    else part.get(
                                        "group", group_for(part.get("targets", []))
                                    )
                                ),
                                files=part.get("targets", []),
                                attempts=0,
                                diff=(
                                    planned[part["id"]].diff
                                    if part["id"] in planned
                                    else ""
                                ),
                                input_tokens=part.get("input_tokens", 0),
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
                    plan,
                ):
                    raise ValueError("A persisted review plan cannot change identity")
                for chunk in planned.values():
                    connection.execute(
                        update(chunk_table)
                        .where(
                            chunk_table.c.review_id == identifier,
                            chunk_table.c.id == chunk.id,
                            chunk_table.c.diff == "",
                        )
                        .values(diff=chunk.diff)
                    )

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

    @staticmethod
    def _resource(identifier, chunk_id):
        return f"review:{identifier}:chunk:{chunk_id}"

    def claim(self, identifier, chunk_id, *, seconds=900):
        resource = self._resource(identifier, chunk_id)
        with self.engine.begin() as connection:
            now = self.executions.now(connection)
            row = (
                connection.execute(
                    select(chunk_table).where(
                        chunk_table.c.review_id == identifier,
                        chunk_table.c.id == chunk_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["status"] not in {"pending", "running"}:
                return None
            active_run = (
                select(runs.c.id)
                .where(
                    runs.c.id == identifier,
                    runs.c.state == "running",
                )
                .exists()
            )
            result = connection.execute(
                update(chunk_table)
                .where(
                    chunk_table.c.review_id == identifier,
                    chunk_table.c.id == chunk_id,
                    chunk_table.c.attempts == row["attempts"],
                    chunk_table.c.status.in_(("pending", "running")),
                    active_run,
                    ~self.executions.active(resource, now),
                )
                .values(status="running", attempts=chunk_table.c.attempts + 1)
            )
            if result.rowcount != 1:
                return None
            return self.executions.start(
                connection, resource, row["attempts"] + 1, seconds=seconds, now=now
            )

    def finish(self, identifier, chunk_id, attempt: ExecutionAttempt, answer):
        if attempt.resource != self._resource(identifier, chunk_id):
            return False
        with self.engine.begin() as connection:
            now = self.executions.now(connection)
            result = connection.execute(
                update(chunk_table)
                .where(
                    chunk_table.c.review_id == identifier,
                    chunk_table.c.id == chunk_id,
                    chunk_table.c.attempts == attempt.attempt,
                    select(runs.c.id)
                    .where(runs.c.id == identifier, runs.c.state == "running")
                    .exists(),
                    self.executions.owns(attempt, now),
                    chunk_table.c.status == "running",
                )
                .values(status="done", result=answer)
            )
            if result.rowcount != 1:
                return False
            if not self.executions.finish(
                connection, attempt, status="succeeded", now=now
            ):
                raise RuntimeError("The execution attempt is no longer active")
            return True

    def attempt_history(self, identifier, chunk_id):
        with self.engine.connect() as connection:
            return self.executions.history(
                connection, self._resource(identifier, chunk_id)
            )

    def answer(self, identifier, chunk_id):
        self.ensure_active(identifier)
        with self.engine.connect() as connection:
            return connection.execute(
                select(chunk_table.c.result).where(
                    chunk_table.c.review_id == identifier,
                    chunk_table.c.id == chunk_id,
                    chunk_table.c.status == "done",
                )
            ).scalar_one_or_none()

    def answers(self, identifier):
        with self.engine.connect() as connection:
            return {
                row["id"]: row["result"]
                for row in connection.execute(
                    select(chunk_table).where(
                        chunk_table.c.review_id == identifier,
                        chunk_table.c.status == "done",
                    )
                ).mappings()
            }

    def chunks(self, identifier):
        from .chunks import ReviewChunk
        from .execution import ReviewTarget

        with self.engine.connect() as connection:
            return tuple(
                ReviewChunk(
                    id=row["id"],
                    review_id=row["review_id"],
                    group=row["group"],
                    files=tuple(
                        ReviewTarget(
                            file["path"],
                            tuple(tuple(range_) for range_ in file.get("ranges", [])),
                        )
                        for file in row["files"]
                    ),
                    status=row["status"],
                    attempts=row["attempts"],
                    result=row["result"],
                    diff=row["diff"],
                    input_tokens=row["input_tokens"],
                )
                for row in connection.execute(
                    select(chunk_table).where(chunk_table.c.review_id == identifier)
                ).mappings()
            )

    def cancel(self, identifier):
        self._stop(identifier, "cancelled")

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
                    select(chunk_table.c.status).where(
                        chunk_table.c.review_id == identifier
                    )
                ).scalars()
            )
            return {
                "run_id": identifier,
                "state": run["state"],
                "model_calls": run["calls"],
                "maximum_model_calls": run["maximum_calls"],
                "chunks": {
                    state: states.count("pending" if state == "queued" else state)
                    for state in ("queued", "running", "done", "failed")
                },
            }

    def release(self, identifier, chunk_id, attempt: ExecutionAttempt, *, error=""):
        if attempt.resource != self._resource(identifier, chunk_id):
            return False
        with self.engine.begin() as connection:
            now = self.executions.now(connection)
            self.executions.expire(connection, [attempt.resource], now)
            result = connection.execute(
                update(chunk_table)
                .where(
                    chunk_table.c.review_id == identifier,
                    chunk_table.c.id == chunk_id,
                    chunk_table.c.attempts == attempt.attempt,
                    chunk_table.c.status == "running",
                    self.executions.owns(attempt, now),
                )
                .values(status="pending")
            )
            if result.rowcount != 1:
                return False
            if not self.executions.finish(
                connection,
                attempt,
                status="failed" if error else "released",
                now=now,
                error=error,
            ):
                raise RuntimeError("The execution attempt is no longer active")
            return True

    def ensure_active(self, identifier):
        with self.engine.connect() as connection:
            state = connection.execute(
                select(runs.c.state).where(runs.c.id == identifier)
            ).scalar_one_or_none()
        if state != "running":
            raise RuntimeError("The review run is no longer active")

    def fail(self, identifier, *, on_failed=None):
        self._stop(identifier, "failed", on_failed=on_failed)

    def _stop(self, identifier, state, *, on_failed=None):
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(runs)
                .where(
                    runs.c.id == identifier,
                    runs.c.state == "running",
                )
                .values(state=state)
            )
            if changed.rowcount != 1:
                return
            connection.execute(
                update(chunk_table)
                .where(
                    chunk_table.c.review_id == identifier,
                    chunk_table.c.status.in_(("pending", "running")),
                )
                .values(status="failed")
            )
            resources = [
                self._resource(identifier, chunk_id)
                for chunk_id in connection.execute(
                    select(chunk_table.c.id).where(
                        chunk_table.c.review_id == identifier
                    )
                ).scalars()
            ]
            self.executions.stop(
                connection,
                resources,
                status=state,
                now=self.executions.now(connection),
                error=f"Review {state}",
            )
            if on_failed is not None:
                on_failed(connection)

    def complete(self, identifier):
        with self.engine.begin() as connection:
            unfinished = (
                select(chunk_table.c.id)
                .where(
                    chunk_table.c.review_id == identifier,
                    chunk_table.c.status != "done",
                )
                .exists()
            )
            result = connection.execute(
                update(runs)
                .where(
                    runs.c.id == identifier,
                    runs.c.state == "running",
                    ~unfinished,
                )
                .values(state="done")
            )
            return result.rowcount == 1
