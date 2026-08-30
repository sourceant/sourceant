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
    delete,
    select,
)

from .records import DONE, FAILED, RUNNING, ReviewRecord

metadata = MetaData()

reviews_table = Table(
    "local_reviews",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("repository", String(500), nullable=False, index=True),
    Column("status", String(32), nullable=False),
    # The whole answer, as it will be read back. A review is written once and
    # read whole; nothing queries inside it, so nothing is gained by taking it
    # apart into columns that would then have to be kept in step with the
    # shape a screen expects.
    Column("answer", Text, nullable=False),
    Column("error", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("started", DateTime, nullable=False),
    Column("finished", DateTime, nullable=True),
)

# A machine accumulates these. Old ones are worth keeping long enough that a
# link still works the next morning, and not much longer.
KEEP = 200


def _when(value) -> datetime | None:
    if value is None:
        return None
    # SQLite hands back a naive datetime whatever went in.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class SQLReviewStore:
    """Reviews, kept where the rest of what this machine knows is kept."""

    def __init__(self, engine: Engine, *, create_schema: bool = False) -> None:
        self._engine = engine
        if create_schema:
            metadata.create_all(engine)

    def put(self, review: ReviewRecord) -> ReviewRecord:
        row = {
            "id": review.id,
            "repository": review.repository,
            "status": review.status,
            "answer": json.dumps(dict(review.answer)),
            "error": review.error,
            "title": review.title,
            "started": review.started,
            "finished": review.finished,
        }
        with self._engine.begin() as connection:
            connection.execute(
                delete(reviews_table).where(reviews_table.c.id == review.id)
            )
            connection.execute(reviews_table.insert().values(**row))
            self._forget_old(connection)
        return review

    def _forget_old(self, connection) -> None:
        """Drop the oldest once there are more than are worth keeping."""
        kept = (
            connection.execute(
                select(reviews_table.c.id)
                .order_by(reviews_table.c.started.desc())
                .limit(KEEP)
            )
            .scalars()
            .all()
        )
        if len(kept) < KEEP:
            return
        connection.execute(delete(reviews_table).where(reviews_table.c.id.notin_(kept)))

    def get(self, identifier: str) -> ReviewRecord | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(reviews_table).where(reviews_table.c.id == identifier)
                )
                .mappings()
                .first()
            )
        return self._read(row) if row else None

    def recent(self, repository: str = "", limit: int = 20) -> tuple[ReviewRecord, ...]:
        query = (
            select(reviews_table).order_by(reviews_table.c.started.desc()).limit(limit)
        )
        if repository:
            query = query.where(reviews_table.c.repository == repository)
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return tuple(self._read(row) for row in rows)

    @staticmethod
    def _read(row) -> ReviewRecord:
        try:
            answer = json.loads(row["answer"] or "{}")
        except (json.JSONDecodeError, TypeError):
            answer = {}
        return ReviewRecord(
            id=row["id"],
            repository=row["repository"],
            status=(
                row["status"] if row["status"] in (RUNNING, DONE, FAILED) else FAILED
            ),
            answer=answer if isinstance(answer, dict) else {},
            error=row["error"] or "",
            title=row["title"] or "",
            started=_when(row["started"]),
            finished=_when(row["finished"]),
        )
