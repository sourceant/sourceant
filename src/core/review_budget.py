from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from time import monotonic


class ReviewStopped(RuntimeError):
    pass


class ReviewContinuation(RuntimeError):
    pass


class ReviewBudgetExceeded(RuntimeError):
    pass


class ReviewBudget:
    def __init__(self, configuration=None):
        def value(key, fallback):
            return configuration.value(key) if configuration is not None else fallback

        self.maximum_calls = int(value("review.maximum_model_calls", 64) or 64)
        self.standard_calls = self.maximum_calls
        self.planned_calls = self.maximum_calls
        self.concurrency = int(value("review.concurrency", 6) or 6)
        self.slots = BoundedSemaphore(self.concurrency)
        self.output_tokens = int(value("review.output_tokens", 8192) or 8192)
        duration = int(value("review.time_budget", 900) or 900)
        self.deadline = monotonic() + duration
        self.continuation_margin = min(60, duration / 10)
        self.calls = 0
        self.exhausted = False
        self.lock = Lock()
        self.work = None
        self.run_id = ""
        self.continuation = False
        self.cancelled = False
        self.stopped = None

    def plan_parts(self, count, configuration, *, calls_per_part=1, extra_calls=0):
        if count < 1 or calls_per_part < 1 or extra_calls < 0:
            raise ValueError("A review needs a positive planned workload")
        if count == 1 and extra_calls == 0:
            return
        import os

        ceiling = min(
            int(configuration.value("review.large_change_maximum_model_calls") or 256),
            int(os.getenv("SOURCEANT_LARGE_REVIEW_MAXIMUM_CALLS", "256")),
        )
        requested = count * self.standard_calls + extra_calls + 2
        required = count * calls_per_part + extra_calls + 2
        available = min(max(self.planned_calls, requested), ceiling)
        if required > available:
            raise ReviewBudgetExceeded(
                f"The plan needs at least {required} model calls, exceeding the planned allowance of {available} (large-PR ceiling {ceiling}) before discovery"
            )
        with self.lock:
            self.planned_calls = max(self.planned_calls, requested)
            self.maximum_calls = min(self.planned_calls, ceiling)

    @contextmanager
    def model_slot(self):
        if not self.slots.acquire(timeout=self.remaining()):
            self.remaining()
            raise TimeoutError("No review model slot became available")
        try:
            yield
        finally:
            self.slots.release()

    def check_current(self):
        if self.stopped is not None and self.stopped():
            self.cancelled = True
            if self.work is not None:
                self.work.cancel(self.run_id)
            raise ReviewStopped("Review no longer wanted")

    def remaining(self):
        self.check_current()
        remaining = self.deadline - monotonic()
        if self.work is not None and remaining <= self.continuation_margin:
            self.continuation = True
            raise ReviewContinuation(
                "The review will continue in another worker window"
            )
        if remaining <= 0:
            self.exhausted = True
            raise ReviewBudgetExceeded(
                "The review reached its time budget. Split the change or increase the review budget in its repository settings."
            )
        return remaining

    def reserve(self):
        with self.lock:
            self.remaining()
            if self.calls >= self.maximum_calls:
                self.exhausted = True
                raise ReviewBudgetExceeded(
                    "The review reached its model-call budget. Split the change or increase the review budget in its repository settings."
                )
            if self.work is not None:
                try:
                    self.work.reserve_call(self.run_id)
                except RuntimeError as error:
                    self.exhausted = True
                    raise ReviewBudgetExceeded(str(error)) from error
            self.calls += 1
