"""Things wrapped around a handler, rather than written into every one of them.

Refusing to overlap is not here. It has to be decided at the moment a job is
claimed, or a job that cannot have its lock would be claimed and then handed
straight back, so the store does it. What is here is everything that can only
be decided once a job is about to run.

A plugin contributes its own the same way it contributes a handler, so a
concern that matters to one kind of work does not become a column everybody
else carries.
"""

from __future__ import annotations

from threading import RLock
from time import monotonic
from typing import Optional

from src.utils.logger import logger

from .models import Job, JobOutcome


class Throttled:
    """Stops asking a provider that has just refused us several times.

    A rate limit reached by one job is reached by the next twenty, and trying
    them anyway turns one refusal into twenty. Worse, with a customer's own key
    paying, those attempts spend somebody's quota to learn what the first one
    already said.

    Failures are counted per key, which is usually the tenant, so one
    customer's exhausted quota does not stop anybody else's work.
    """

    def __init__(
        self, *, allowance: int = 5, window: float = 60.0, pause: float = 60.0
    ):
        self._allowance = allowance
        self._window = window
        self._pause = pause
        self._lock = RLock()
        self._failures: dict[str, list[float]] = {}
        self._paused_until: dict[str, float] = {}

    def _key(self, job: Job) -> str:
        return job.tenant or job.kind

    def before(self, job: Job) -> Optional[JobOutcome]:
        with self._lock:
            until = self._paused_until.get(self._key(job), 0.0)
            waiting = until - monotonic()
        if waiting <= 0:
            return None
        logger.info(f"Holding {job.kind} for {int(waiting)}s after repeated refusals")
        return JobOutcome.failed(
            "held back after repeated refusals", retry=True, retry_in=int(waiting) + 1
        )

    def after(self, job: Job, outcome: JobOutcome) -> JobOutcome:
        key = self._key(job)
        now = monotonic()
        with self._lock:
            if outcome.succeeded:
                self._failures.pop(key, None)
                return outcome
            recent = [
                at for at in self._failures.get(key, []) if now - at < self._window
            ]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self._allowance:
                self._paused_until[key] = now + self._pause
                self._failures[key] = []
                logger.warning(
                    f"{key} refused {len(recent)} times; pausing it for {self._pause}s"
                )
        return outcome


class RateLimited:
    """Keeps a key under a fixed number of starts in a window."""

    def __init__(self, *, starts: int = 60, window: float = 60.0):
        self._starts = starts
        self._window = window
        self._lock = RLock()
        self._seen: dict[str, list[float]] = {}

    def before(self, job: Job) -> Optional[JobOutcome]:
        key = job.tenant or job.kind
        now = monotonic()
        with self._lock:
            recent = [at for at in self._seen.get(key, []) if now - at < self._window]
            if len(recent) >= self._starts:
                oldest = recent[0]
                self._seen[key] = recent
                wait = int(self._window - (now - oldest)) + 1
                return JobOutcome.failed("rate limited", retry=True, retry_in=wait)
            recent.append(now)
            self._seen[key] = recent
        return None

    def after(self, job: Job, outcome: JobOutcome) -> JobOutcome:
        return outcome
