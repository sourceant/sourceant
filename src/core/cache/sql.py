"""A cache kept where everything else is kept."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from src.config.db import get_engine
from src.models.cache_entry import CacheEntry
from src.utils.logger import logger


def _table():
    return CacheEntry.__table__


def _now(connection) -> datetime:
    """Now, according to the database, in UTC and without a timezone on it.

    What writes an entry and what reads it back are not the same process and
    need not agree on the time. Reading the deadline against one clock and
    setting it by another expires an entry that has only just been written.

    Postgres answers with a timezone and SQLite without one, and the column
    holds neither, so the timezone is resolved here rather than left to differ
    by engine.
    """
    moment = connection.execute(
        sa.select(sa.func.current_timestamp(type_=sa.DateTime))
    ).scalar()
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


class SQLCache:
    """Nothing has to be running for this to work, which is the point.

    A cache that needs a second service is unavailable exactly when that
    service is, and what is cached is usually wanted most under load.
    """

    def __init__(self, *, create_schema: bool = False) -> None:
        self._created = not create_schema

    def _ready(self, engine) -> None:
        """Build the table where nothing else has.

        Migrations are what a deployment runs, but a personal machine started
        with `sourceant serve` runs none, and a missing table would turn every
        read into a warning on a path that is meant to be quiet.
        """
        if self._created:
            return
        _table().create(engine, checkfirst=True)
        self._created = True

    def _where(self, namespace: str, key: str):
        return sa.and_(_table().c.namespace == namespace, _table().c.key == key)

    def get(self, namespace: str, key: str) -> Optional[str]:
        try:
            # Asked for inside the guard, not before it. Reaching the database
            # is itself something that fails, and a caller must not be brought
            # down because the place its cache lives could not be reached.
            engine = get_engine()
            if engine is None:
                return None
            self._ready(engine)
            with engine.connect() as connection:
                return connection.execute(
                    sa.select(_table().c.value).where(
                        sa.and_(
                            self._where(namespace, key),
                            _table().c.expires_at > _now(connection),
                        )
                    )
                ).scalar()
        except Exception as e:
            logger.warning(f"Could not read the cache: {e}")
            return None

    def set(self, namespace: str, key: str, value: str, *, ttl: int = 0) -> None:
        if ttl <= 0:
            return
        try:
            engine = get_engine()
            if engine is None:
                return
            self._ready(engine)
            with engine.begin() as connection:
                now = _now(connection)
                kept = {
                    "value": value,
                    "expires_at": now + timedelta(seconds=ttl),
                    "updated_at": now,
                }
                written = connection.execute(
                    sa.update(_table())
                    .where(self._where(namespace, key))
                    .values(**kept)
                ).rowcount
                if not written:
                    self._first(connection, namespace, key, now, kept)
                connection.execute(
                    sa.delete(_table()).where(_table().c.expires_at <= now)
                )
        except Exception as e:
            logger.warning(f"Could not write the cache: {e}")

    def forget(self, namespace: str, key: str) -> None:
        try:
            engine = get_engine()
            if engine is None:
                return
            self._ready(engine)
            with engine.begin() as connection:
                connection.execute(
                    sa.delete(_table()).where(self._where(namespace, key))
                )
        except Exception as e:
            logger.warning(f"Could not clear the cache: {e}")

    def _first(
        self, connection, namespace: str, key: str, now: datetime, kept: Dict[str, Any]
    ) -> None:
        """Write an entry nothing has written before.

        Two callers working out the same thing can reach here at once, both
        finding nothing to update. The second is not an error: it is the same
        value, so it takes the row the first made.
        """
        try:
            with connection.begin_nested():
                connection.execute(
                    sa.insert(_table()).values(
                        namespace=namespace, key=key, created_at=now, **kept
                    )
                )
        except IntegrityError:
            connection.execute(
                sa.update(_table()).where(self._where(namespace, key)).values(**kept)
            )
