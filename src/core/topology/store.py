"""Where topology is kept, resolved the same way wherever it is asked for.

A request and a worker both need the store, and a worker that resolved it
differently would write a discovery somewhere the request that asked for it
cannot read.
"""

from __future__ import annotations

from typing import Optional

from src.config.db import get_engine
from src.core.services import ServiceRegistry, service_registry

from .interfaces import TopologyRepository
from .memory import InMemoryTopologyRepository
from .sql import SQLTopologyRepository

_fallback: Optional[TopologyRepository] = None


def topology_repository(
    services: ServiceRegistry = service_registry,
) -> TopologyRepository:
    """The plugin-provided repository when one is registered, else core's own.

    ``ServiceRegistry.register`` allows a single provider per interface, so core
    cannot pre-register alongside a plugin. Resolution has to happen per call.
    """
    global _fallback
    try:
        return services.resolve(TopologyRepository)
    except LookupError:
        pass
    if _fallback is None:
        engine = get_engine()
        _fallback = (
            SQLTopologyRepository(engine)
            if engine is not None
            else InMemoryTopologyRepository()
        )
    return _fallback
