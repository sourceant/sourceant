"""Background work: asked for here, done somewhere else, and accounted for.

Work is separated into lanes named by the latency they promise, so a scan that
takes hours cannot sit in front of a webhook that has seconds. Within a lane
every tenant takes an equal turn up to a fixed cap, so one customer's backlog
cannot starve another's.

A claim on a job is a lease rather than a promise. A worker that is killed
writes nothing, so nothing running inside it could be what recovers its work:
instead the claim simply lapses and the next poll offers the job again. That is
also why delivery is at least once, and why work that must not be repeated says
so with `max_attempts=1` rather than hoping.
"""

from __future__ import annotations

from typing import Any, Optional

from src.config.db import get_engine
from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger

from .backoff import after as backoff_after
from .fairness import share
from .interfaces import (
    JobAdmin,
    JobBatches,
    JobHandler,
    JobMiddleware,
    JobQueue,
    JobStore,
    JobWakeup,
)
from .memory import InMemoryJobQueue, InMemoryJobStore
from .middleware import RateLimited, Throttled
from .models import (
    BY_NOBODY,
    BY_REPOSITORY,
    BY_WORKSPACE,
    CANCELLED,
    DEAD,
    FAILED,
    LANES,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    WITHIN_HOURS,
    WITHIN_MINUTES,
    WITHIN_SECONDS,
    Batch,
    Candidate,
    Job,
    JobOutcome,
    JobRequest,
    Lease,
)
from .sql import SQLJobStore
from .worker import Worker

_core = None


def _default():
    """The core's own store, built the first time anything asks for one.

    A deployment with a database gets the durable one. A machine running
    without one still gets a working queue, in this process only, because
    refusing to accept work is worse than accepting it without durability
    somewhere that never had any to begin with.
    """
    global _core
    if _core is None:
        engine = get_engine()
        if engine is None:
            logger.info("No database, so background work is kept in this process only.")
            _core = InMemoryJobStore()
        else:
            _core = SQLJobStore(engine, create_schema=True)
    return _core


def job_store(services: ServiceRegistry = service_registry) -> JobStore:
    """Whatever registered as a store, else the core's own."""
    try:
        return services.resolve(JobStore)
    except LookupError:
        return _default()


def job_queue(services: ServiceRegistry = service_registry) -> JobQueue:
    """Whatever registered as a queue, else whatever is storing the jobs."""
    try:
        return services.resolve(JobQueue)
    except LookupError:
        return job_store(services)


def enqueue(
    request: JobRequest,
    *,
    session: Any = None,
    services: ServiceRegistry = service_registry,
) -> int:
    """Ask for work, in the caller's transaction where one is given."""
    return job_queue(services).enqueue(request, session=session)


def handlers(services: ServiceRegistry = service_registry) -> tuple[JobHandler, ...]:
    """Every handler contributed, whatever it came from.

    Contributed rather than registered, because handlers are additive: a plugin
    adding one is not answering a question the core already had an answer to.
    """
    return services.contributions(JobHandler)


def handler_for(
    kind: str, services: ServiceRegistry = service_registry
) -> Optional[JobHandler]:
    """The handler for one kind of work, or None when nothing claims it."""
    for handler in handlers(services):
        if getattr(handler, "kind", None) == kind:
            return handler
    return None


def middleware(
    services: ServiceRegistry = service_registry,
) -> tuple[JobMiddleware, ...]:
    """Everything wrapped around a handler before it runs."""
    return services.contributions(JobMiddleware)


__all__ = [
    "BY_NOBODY",
    "BY_REPOSITORY",
    "BY_WORKSPACE",
    "Batch",
    "CANCELLED",
    "Candidate",
    "DEAD",
    "FAILED",
    "InMemoryJobQueue",
    "InMemoryJobStore",
    "Job",
    "JobAdmin",
    "JobBatches",
    "JobHandler",
    "JobMiddleware",
    "JobOutcome",
    "JobQueue",
    "JobRequest",
    "JobStore",
    "JobWakeup",
    "LANES",
    "Lease",
    "QUEUED",
    "RUNNING",
    "RateLimited",
    "SQLJobStore",
    "SUCCEEDED",
    "Throttled",
    "WITHIN_HOURS",
    "WITHIN_MINUTES",
    "WITHIN_SECONDS",
    "Worker",
    "backoff_after",
    "enqueue",
    "handler_for",
    "handlers",
    "job_queue",
    "job_store",
    "middleware",
    "share",
]
