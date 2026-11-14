from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import token_hex

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    insert,
    select,
    update,
)

metadata = MetaData()
execution_attempts = Table(
    "execution_attempts",
    metadata,
    Column("claim_token", String(64), primary_key=True),
    Column("resource", String(255), nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("claim_expires_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=True),
    Column("error", Text, nullable=False),
    UniqueConstraint("resource", "attempt", name="uq_execution_attempt_number"),
    Index("ix_execution_attempt_resource", "resource", "status", "claim_expires_at"),
)


@dataclass(frozen=True)
class ExecutionAttempt:
    claim_token: str
    resource: str
    attempt: int
    status: str
    started_at: datetime
    claim_expires_at: datetime
    finished_at: datetime | None = None
    error: str = ""


class SQLExecutionAttempts:
    @staticmethod
    def now(connection):
        value = connection.execute(
            select(func.current_timestamp(type_=DateTime))
        ).scalar_one()
        return (
            value.astimezone(timezone.utc).replace(tzinfo=None)
            if value.tzinfo
            else value
        )

    @staticmethod
    def active(resource, now):
        return (
            select(execution_attempts.c.claim_token)
            .where(
                execution_attempts.c.resource == resource,
                execution_attempts.c.status == "running",
                execution_attempts.c.claim_expires_at > now,
            )
            .exists()
        )

    @staticmethod
    def owns(attempt, now):
        return (
            select(execution_attempts.c.claim_token)
            .where(
                execution_attempts.c.claim_token == attempt.claim_token,
                execution_attempts.c.resource == attempt.resource,
                execution_attempts.c.attempt == attempt.attempt,
                execution_attempts.c.status == "running",
                execution_attempts.c.claim_expires_at > now,
            )
            .exists()
        )

    @staticmethod
    def expire(connection, resources, now):
        connection.execute(
            update(execution_attempts)
            .where(
                execution_attempts.c.resource.in_(resources),
                execution_attempts.c.status == "running",
                execution_attempts.c.claim_expires_at <= now,
            )
            .values(
                status="expired",
                finished_at=now,
                error="Claim expired",
            )
        )

    def start(self, connection, resource, number, *, seconds, now):
        """The caller must atomically admit this resource in the same transaction."""
        self.expire(connection, [resource], now)
        if connection.execute(select(self.active(resource, now))).scalar_one():
            raise RuntimeError("An execution attempt already owns this resource")
        token = token_hex(24)
        until = now + timedelta(seconds=seconds)
        connection.execute(
            insert(execution_attempts).values(
                claim_token=token,
                resource=resource,
                attempt=number,
                status="running",
                started_at=now,
                claim_expires_at=until,
                error="",
            )
        )
        return ExecutionAttempt(token, resource, number, "running", now, until)

    @staticmethod
    def finish(connection, attempt, *, status, now, error=""):
        if status not in {"succeeded", "failed", "released", "cancelled"}:
            raise ValueError("Invalid execution attempt outcome")
        result = connection.execute(
            update(execution_attempts)
            .where(
                execution_attempts.c.claim_token == attempt.claim_token,
                execution_attempts.c.resource == attempt.resource,
                execution_attempts.c.attempt == attempt.attempt,
                execution_attempts.c.status == "running",
                execution_attempts.c.claim_expires_at > now,
            )
            .values(status=status, finished_at=now, error=error)
        )
        return result.rowcount == 1

    def stop(self, connection, resources, *, status, now, error=""):
        if status not in {"failed", "cancelled"}:
            raise ValueError("Invalid execution stop outcome")
        self.expire(connection, resources, now)
        connection.execute(
            update(execution_attempts)
            .where(
                execution_attempts.c.resource.in_(resources),
                execution_attempts.c.status == "running",
            )
            .values(status=status, finished_at=now, error=error)
        )

    @staticmethod
    def history(connection, resource):
        return tuple(
            ExecutionAttempt(**row)
            for row in connection.execute(
                select(execution_attempts)
                .where(execution_attempts.c.resource == resource)
                .order_by(execution_attempts.c.attempt)
            ).mappings()
        )
