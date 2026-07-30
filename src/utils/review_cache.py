"""Reuse of generated reviews.

Generating a review costs a model run, so one is kept per revision and served
again until the change moves. The key carries the revision, so a new commit
misses rather than needing invalidation.
"""

import json
from typing import Any, Dict, Optional

from src.config.settings import REDIS_HOST, REDIS_PORT
from src.utils.logger import logger

# A review is only worth reusing while the pull request is still being worked on.
TTL_SECONDS = 7 * 24 * 60 * 60

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


def get_review(
    repo_full_name: str, pr_number: int, head_sha: Optional[str]
) -> Optional[Dict[str, Any]]:
    if not head_sha:
        return None
    client = _redis()
    if client is None:
        return None
    try:
        cached = client.get(_key(repo_full_name, pr_number, head_sha))
        return json.loads(cached) if cached else None
    except Exception as e:
        logger.warning(f"Could not read the review cache: {e}")
        return None


def save_review(
    repo_full_name: str,
    pr_number: int,
    head_sha: Optional[str],
    payload: Dict[str, Any],
) -> None:
    if not head_sha:
        return
    client = _redis()
    if client is None:
        return
    try:
        client.setex(
            _key(repo_full_name, pr_number, head_sha), TTL_SECONDS, json.dumps(payload)
        )
    except Exception as e:
        logger.warning(f"Could not write the review cache: {e}")
