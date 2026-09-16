"""Marks a pull request whose review is no longer wanted."""

from __future__ import annotations

from src.config.settings import whole_number
from src.core.cache import cache, keyed
from src.utils.logger import logger

NAMESPACE = "review.abandoned"
CURRENT = "review.revision"

KEPT_FOR = whole_number("REVIEW_ABANDONED_SECONDS", 6 * 60 * 60)


def _key(repository: str, number: int) -> str:
    return keyed(repository, str(number))


def abandon(repository: str, number: int) -> None:
    """Mark this pull request as no longer worth reviewing."""
    if not repository or not number:
        return
    try:
        cache().set(NAMESPACE, _key(repository, number), "abandoned", ttl=KEPT_FOR)
    except Exception:  # noqa: BLE001
        logger.warning("Could not abandon %s#%s", repository, number, exc_info=True)


def resume(repository: str, number: int) -> None:
    """Forget that this pull request was abandoned."""
    if not repository or not number:
        return
    try:
        cache().forget(NAMESPACE, _key(repository, number))
    except Exception:  # noqa: BLE001
        logger.warning("Could not resume %s#%s", repository, number, exc_info=True)


def supersede(repository: str, number: int, revision: str) -> None:
    """Record the revision now being reviewed."""
    if not repository or not number or not revision:
        return
    try:
        cache().set(CURRENT, _key(repository, number), revision, ttl=KEPT_FOR)
    except Exception:  # noqa: BLE001
        logger.warning("Could not supersede %s#%s", repository, number, exc_info=True)


def abandoned(repository: str, number: int, revision: str = "") -> bool:
    """Whether this review is no longer wanted. False if it cannot be read.

    A review is no longer wanted once its pull request is closed, or once a
    revision other than the one it is reading becomes the current one.
    """
    if not repository or not number:
        return False
    try:
        if cache().get(NAMESPACE, _key(repository, number)):
            return True
        if not revision:
            return False
        current = cache().get(CURRENT, _key(repository, number))
        return bool(current) and current != revision
    except Exception:  # noqa: BLE001
        logger.warning("Could not ask about %s#%s", repository, number, exc_info=True)
        return False
