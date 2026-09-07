"""Tidying up after the queue.

Nothing here is on the critical path, and that is the point. Recovering work
from a worker that died is done by the claim itself, so this can lag, fail, or
never run at all on a given deployment without work being lost. What it does is
stop the table growing without limit, which is the way a queue in a database
goes wrong slowly rather than loudly.

It is itself a job, so it needs no scheduler: each run asks for the next one.
"""

from __future__ import annotations

from typing import Any, Optional

from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger

from .models import WITHIN_MINUTES, JobOutcome, JobRequest

#: How often the tidying runs, and the key that stops two being queued at once.
SWEEP_KIND = "jobs.sweep"
SWEEP_SLOT = "jobs.sweep"
EVERY_SECONDS = 3600


class Sweeper:
    """Clears away what the queue has finished with."""

    kind = SWEEP_KIND

    def __init__(
        self,
        store: Any = None,
        *,
        keep_finished_for_days: int = 14,
        every_seconds: int = EVERY_SECONDS,
        services: ServiceRegistry = service_registry,
    ) -> None:
        self._store = store
        self._days = keep_finished_for_days
        self._every = every_seconds
        self._services = services

    def _own(self):
        if self._store is not None:
            return self._store
        from . import job_store

        return job_store(self._services)

    def run(self, job=None) -> JobOutcome:
        store = self._own()
        try:
            cleared = store.prune(self._days)
        except Exception as error:
            logger.warning(f"Could not tidy the queue: {error}")
            return JobOutcome.failed(f"{type(error).__name__}: {error}", retry=True)
        if cleared:
            logger.info(f"Cleared {cleared} finished jobs")
        if self.arrange(store) is None:
            # Each tidy asks for the next, so one that cannot is the end of all
            # tidying. Failing here has it tried again instead.
            return JobOutcome.failed("could not ask for the next tidy", retry=True)
        return JobOutcome.ok()

    def arrange(self, store=None) -> Optional[int]:
        """Ask for the next tidying.

        The dedupe key means asking twice queues one, so this is safe to call
        whenever, including from something starting up that cannot know whether
        a sweep is already waiting.
        """
        store = store or self._own()
        try:
            return store.enqueue(
                JobRequest(
                    lane=WITHIN_MINUTES,
                    kind=SWEEP_KIND,
                    dedupe_slot=SWEEP_SLOT,
                    delay_seconds=self._every,
                    deadline_seconds=300,
                    max_attempts=3,
                    priority=-10,
                )
            )
        except Exception:
            logger.warning(
                "Could not arrange the next tidy of the queue", exc_info=True
            )
            return None
