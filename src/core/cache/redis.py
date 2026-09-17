"""A cache kept in Redis, for a deployment that already runs one."""

from __future__ import annotations

import time
from typing import Optional

from src.config.settings import REDIS_HOST, REDIS_PORT
from src.core.cache.interfaces import Owner
from src.utils.logger import logger

# How long to leave a cache that would not answer alone before trying it
# again. Long enough that a cache which is down is not dialled on every read,
# short enough that one which came back is used again without a restart.
COOLDOWN = 60


class RedisCache:
    def __init__(self, db: int = 2, cooldown: int = COOLDOWN) -> None:
        self._db = db
        self._cooldown = cooldown
        self._client = None
        self._retry_at = 0.0
        self._reported = False

    def _connected(self):
        """Best effort, and tried again later rather than given up on.

        A cache is reached over a network, so it can fail for a minute and be
        fine afterwards. Refusing it for the life of the process means one bad
        minute costs every review until somebody restarts this.
        """
        if self._client is not None:
            return self._client
        if time.monotonic() < self._retry_at:
            return None
        try:
            import redis

            client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=self._db)
            client.ping()
            self._client = client
            self._reported = False
        except Exception as e:
            # Said once for an outage, not once per attempt. A cache that is
            # down is down for every read, and saying so each minute buries
            # whatever else the log was for.
            if not self._reported:
                logger.warning(f"Cache unreachable, nothing will be reused: {e}")
                self._reported = True
            self._retry_at = time.monotonic() + self._cooldown
        return self._client

    @staticmethod
    def _named(namespace: str, key: str) -> str:
        return f"{namespace}:{key}"

    @staticmethod
    def _belonging(namespace: str, scope: Owner) -> str:
        """The set naming what one scope has in one namespace.

        A key here is a hash, so nothing in it says who the entry was for.
        Reading it back by scope means writing down the membership as it goes.
        """
        return f"{namespace}:belongs:{scope.type}:{scope.id}"

    def get(self, namespace: str, key: str) -> Optional[str]:
        client = self._connected()
        if client is None:
            return None
        try:
            kept = client.get(self._named(namespace, key))
        except Exception as e:
            logger.warning(f"Could not read the cache: {e}")
            self._dropped()
            return None
        return kept.decode() if isinstance(kept, bytes) else kept

    def set(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        ttl: int = 0,
        scope: Optional[Owner] = None,
    ) -> None:
        client = self._connected()
        if client is None or ttl <= 0:
            return
        try:
            client.setex(self._named(namespace, key), ttl, value)
            if scope is not None:
                belonging = self._belonging(namespace, scope)
                client.sadd(belonging, key)
                # Not GT: a key with no expiry counts as infinite for that, so
                # the set the sadd just made would never be given one at all.
                client.expire(belonging, ttl)
        except Exception as e:
            logger.warning(f"Could not write the cache: {e}")
            self._dropped()

    def forget(self, namespace: str, key: str) -> None:
        client = self._connected()
        if client is None:
            return
        try:
            client.delete(self._named(namespace, key))
        except Exception as e:
            logger.warning(f"Could not clear the cache: {e}")
            self._dropped()

    def clear(self, namespace: str, scope: Optional[Owner] = None) -> int:
        client = self._connected()
        if client is None:
            return 0
        try:
            if scope is not None:
                belonging = self._belonging(namespace, scope)
                keys = [
                    self._named(namespace, k.decode() if isinstance(k, bytes) else k)
                    for k in client.smembers(belonging)
                ]
                dropped = client.delete(*keys) if keys else 0
                client.delete(belonging)
                return int(dropped)
            dropped = 0
            batch = []
            for found in client.scan_iter(match=f"{namespace}:*", count=500):
                batch.append(found)
                if len(batch) >= 500:
                    dropped += client.delete(*batch)
                    batch = []
            if batch:
                dropped += client.delete(*batch)
            return int(dropped)
        except Exception as e:
            logger.warning(f"Could not clear the cache: {e}")
            self._dropped()
            return 0

    def _dropped(self) -> None:
        """Let go of a client that stopped answering, so the next call redials."""
        self._client = None
        self._retry_at = time.monotonic() + self._cooldown
