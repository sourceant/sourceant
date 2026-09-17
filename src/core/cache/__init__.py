"""Somewhere to keep what can be worked out again.

Anything keyed, expiring and reproducible belongs here: what a model answered,
what a search returned, what a forge said about a file. The caller decides what
goes in a key and how long it is worth keeping. This decides where it lives and
what happens when that place is unreachable, which is nothing.
"""

from __future__ import annotations

import hashlib

from src.config.settings import choice
from src.core.services import ServiceRegistry, service_registry

from .interfaces import Cache, Owner
from .redis import RedisCache
from .sql import SQLCache

# The same choice the review cache offers, read the same way, so a deployment
# that has already said where one goes does not say it twice.
CACHE_BACKEND = choice("REVIEW_CACHE", ("database", "redis"), "database")

_core: Cache = (
    RedisCache() if CACHE_BACKEND == "redis" else SQLCache(create_schema=True)
)


def cache(services: ServiceRegistry = service_registry) -> Cache:
    """Whatever registered as a cache, else core's own."""
    try:
        return services.resolve(Cache)
    except LookupError:
        return _core


def keyed(*parts: str) -> str:
    """One name for one set of things, of whatever length.

    Hashed because a key made of a prompt and a model name is longer than
    anywhere sensible will index, and because the parts are joined on a
    character none of them can contain.
    """
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def owner(attribution) -> "Owner | None":
    """The narrowest thing an entry can be said to belong to.

    A repository before the workspace holding it, so clearing one repository
    does not take the rest of the workspace with it. Only these two: an entry
    recorded against anything the clear cannot name could never be dropped.
    """
    for kind in ("repository", "workspace"):
        named = (attribution or {}).get(kind)
        if named:
            return Owner(kind, str(named))
    return None


#: What can be cleared, and what to call it.
CLEARABLE = {
    "model-response": "Model answers",
    "topology.reading": "Connection readings",
    "review.abandoned": "Stopped reviews",
    "review.revision": "Reviewed revisions",
}


__all__ = [
    "CLEARABLE",
    "Cache",
    "RedisCache",
    "SQLCache",
    "Owner",
    "cache",
    "keyed",
    "owner",
]
