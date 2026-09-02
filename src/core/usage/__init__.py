"""What model calls consumed. Kept in the deployment's database unless a plugin
registers somewhere else."""

from __future__ import annotations

from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger

from .interfaces import UsageRecorder
from .models import ModelUsage
from .sql import SQLUsageRecorder

_core = SQLUsageRecorder()


def usage_recorder(services: ServiceRegistry = service_registry) -> UsageRecorder:
    """Whatever registered as a recorder, else core's database-backed one."""
    try:
        return services.resolve(UsageRecorder)
    except LookupError:
        return _core


def record(usage: ModelUsage, services: ServiceRegistry = service_registry) -> None:
    """Keep what a call consumed, and never let that stop the caller."""
    try:
        usage_recorder(services).record(usage)
    except Exception:
        logger.warning("Could not record what a model call consumed", exc_info=True)


__all__ = [
    "ModelUsage",
    "SQLUsageRecorder",
    "UsageRecorder",
    "record",
    "usage_recorder",
]
