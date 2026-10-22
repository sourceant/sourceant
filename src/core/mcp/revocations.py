"""MCP tokens withdrawn before they expire.

A token is verified by its signature alone, so a withdrawn one keeps working
unless its id is refused here. A row is only needed until the token would have
expired anyway, so expired rows are removed as new ones arrive.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, MetaData, String, Table, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

metadata = MetaData()

revoked_tokens = Table(
    "mcp_revoked_tokens",
    metadata,
    Column("token_id", String(64), primary_key=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)


def revoke(engine: Engine, token_id: str, expires_at: datetime) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            delete(revoked_tokens).where(revoked_tokens.c.expires_at < now)
        )
        if engine.dialect.name == "postgresql":
            connection.execute(
                pg_insert(revoked_tokens)
                .values(token_id=token_id, expires_at=expires_at)
                .on_conflict_do_nothing()
            )
        elif not connection.execute(
            select(revoked_tokens.c.token_id).where(
                revoked_tokens.c.token_id == token_id
            )
        ).first():
            connection.execute(
                revoked_tokens.insert().values(token_id=token_id, expires_at=expires_at)
            )


def is_revoked(engine: Engine, token_id: str) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                select(revoked_tokens.c.token_id).where(
                    revoked_tokens.c.token_id == token_id
                )
            ).first()
            is not None
        )
