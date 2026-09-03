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


def record_completion(
    response,
    *,
    model: str,
    purpose: str = "",
    services: ServiceRegistry = service_registry,
    **attribution,
) -> None:
    """Keep what an answer consumed, whatever shape it came back in.

    Providers report the same two numbers under different names, and a caller
    that has an answer should not have to know which. One that never reports
    them records nothing rather than a row of zeroes, which would read as a
    call that cost nothing.
    """
    counted = getattr(response, "usage", None)
    if counted is None:
        return

    def number(*names: str) -> int:
        for name in names:
            value = getattr(counted, name, None)
            if value is not None:
                return int(value)
        return 0

    given = number("prompt_tokens", "input_tokens")
    answered = number("completion_tokens", "output_tokens")
    if not given and not answered:
        return

    hidden = getattr(response, "_hidden_params", None) or {}
    record(
        ModelUsage(
            model=model,
            input_tokens=given,
            output_tokens=answered,
            cost=hidden.get("response_cost"),
            purpose=purpose,
            **attribution,
        ),
        services,
    )


__all__ = [
    "ModelUsage",
    "SQLUsageRecorder",
    "UsageRecorder",
    "record",
    "record_completion",
    "usage_recorder",
]
