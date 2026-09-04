"""What model calls consumed. Kept in the deployment's database unless a plugin
registers somewhere else."""

from __future__ import annotations

from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger

from .interfaces import UsageRecorder
from .models import TokenUsage
from .sql import SQLUsageRecorder

_core = SQLUsageRecorder(create_schema=True)


def usage_recorder(services: ServiceRegistry = service_registry) -> UsageRecorder:
    """Whatever registered as a recorder, else core's database-backed one."""
    try:
        return services.resolve(UsageRecorder)
    except LookupError:
        return _core


def record(usage: TokenUsage, services: ServiceRegistry = service_registry) -> None:
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

    Providers report the same numbers under different names, and a caller that
    has an answer should not have to know which. An answer that reports none of
    them and no cost records nothing, rather than a row of zeroes that would
    read as a call that cost nothing.

    The whole reading is guarded. The answer it describes has already been paid
    for and is on its way back to somebody, so nothing here may raise into the
    caller.
    """
    try:
        counted = getattr(response, "usage", None)

        def number(*names: str) -> int:
            for name in names:
                value = getattr(counted, name, None)
                if isinstance(value, (int, float)):
                    return int(value)
            return 0

        given = number("prompt_tokens", "input_tokens")
        answered = number("completion_tokens", "output_tokens")
        total = number("total_tokens")

        hidden = getattr(response, "_hidden_params", None)
        cost = hidden.get("response_cost") if isinstance(hidden, dict) else None

        if not given and not answered and not total and cost is None:
            return

        provider, _, _ = model.rpartition("/")
        owner_type, owner_id, subject_type, subject_id = TokenUsage.owed_by(
            **attribution
        )
        usage = TokenUsage(
            provider=provider,
            model=model,
            input_tokens=given,
            output_tokens=answered,
            reported_total=total,
            cost_micro=TokenUsage.micro(cost),
            purpose=purpose,
            owner_type=owner_type,
            owner_id=owner_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
    except Exception:
        logger.warning("Could not read what a model call consumed", exc_info=True)
        return

    record(usage, services)


__all__ = [
    "TokenUsage",
    "SQLUsageRecorder",
    "UsageRecorder",
    "record",
    "record_completion",
    "usage_recorder",
]
