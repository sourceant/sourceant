"""Reuse of generated reviews.

Generating a review costs a model run, so one is kept per revision and served
again until the change moves. The key carries the revision, so a new commit
misses rather than needing invalidation. How long a review is worth reusing is
a judgement about the repository, so it is asked of the repository rather than
fixed here.

A review is kept where everything else is kept. Redis is still an option, and
a deployment that wants it says so, but nothing needs a second thing running to
get the saving.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import sqlalchemy as sa

from src.config.db import get_engine
from src.config.settings import REDIS_HOST, REDIS_PORT
from src.core.settings import value_of
from src.models.cached_review import CachedReview
from src.utils.logger import logger

SECONDS_PER_DAY = 24 * 60 * 60

REVIEW_CACHE = os.getenv("REVIEW_CACHE", "database").lower()

_client = None
_unavailable = False


def _redis():
    """The cache is best effort: a review still generates when Redis is absent."""
    global _client, _unavailable
    if _client is not None or _unavailable:
        return _client
    try:
        import redis

        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1)
        client.ping()
        _client = client
    except Exception as e:
        logger.warning(f"Review cache unavailable, reviews will regenerate: {e}")
        _unavailable = True
    return _client


def _key(repo_full_name: str, pr_number: int, head_sha: str) -> str:
    return f"review:{repo_full_name}:{pr_number}:{head_sha}"


def _table():
    return CachedReview.__table__


def get_review(
    repo_full_name: str, pr_number: int, head_sha: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not head_sha:
        return None
    key = _key(repo_full_name, pr_number, head_sha)
    if REVIEW_CACHE == "redis":
        return _read_from_redis(key)
    return _read_from_database(key)


def _read_from_redis(key: str) -> Optional[Dict[str, Any]]:
    client = _redis()
    if client is None:
        return None
    try:
        cached = client.get(key)
        return json.loads(cached) if cached else None
    except Exception as e:
        logger.warning(f"Could not read the review cache: {e}")
        return None


def _read_from_database(key: str) -> Optional[Dict[str, Any]]:
    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.connect() as connection:
            row = connection.execute(
                sa.select(_table().c.payload).where(
                    sa.and_(
                        _table().c.key == key,
                        _table().c.expires_at > sa.func.current_timestamp(),
                    )
                )
            ).scalar()
        return json.loads(row) if row else None
    except Exception as e:
        logger.warning(f"Could not read the review cache: {e}")
        return None


def _ttl_seconds(repo_full_name: str) -> int:
    """How long this repository reuses a review, as it or its organisation says."""
    days = value_of("review.reuse_days", repository=repo_full_name)
    return int(days) * SECONDS_PER_DAY


def save_review(
    repo_full_name: str,
    pr_number: int,
    head_sha: Optional[str],
    payload: Dict[str, Any],
) -> None:
    if not head_sha:
        return
    ttl = _ttl_seconds(repo_full_name)
    # Reuse turned off entirely means nothing is worth storing.
    if ttl <= 0:
        return
    key = _key(repo_full_name, pr_number, head_sha)
    if REVIEW_CACHE == "redis":
        _write_to_redis(key, ttl, payload)
        return
    _write_to_database(key, ttl, payload)


def _write_to_redis(key: str, ttl: int, payload: Dict[str, Any]) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.setex(key, ttl, json.dumps(payload))
    except Exception as e:
        logger.warning(f"Could not write the review cache: {e}")


def _write_to_database(key: str, ttl: int, payload: Dict[str, Any]) -> None:
    engine = get_engine()
    if engine is None:
        return
    now = datetime.utcnow()
    values = {
        "key": key,
        "payload": json.dumps(payload),
        "expires_at": now + timedelta(seconds=ttl),
        "created_at": now,
        "updated_at": now,
    }
    try:
        with engine.begin() as connection:
            written = connection.execute(
                sa.update(_table())
                .where(_table().c.key == key)
                .values(
                    payload=values["payload"],
                    expires_at=values["expires_at"],
                    updated_at=now,
                )
            ).rowcount
            if not written:
                connection.execute(sa.insert(_table()).values(**values))
            connection.execute(
                sa.delete(_table()).where(
                    _table().c.expires_at <= sa.func.current_timestamp()
                )
            )
    except Exception as e:
        logger.warning(f"Could not write the review cache: {e}")
