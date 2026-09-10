from __future__ import annotations

from typing import Any, Optional, Protocol, Sequence, runtime_checkable

from .models import Batch, Job, JobOutcome, JobRequest, Lease


@runtime_checkable
class JobQueue(Protocol):
    """Where work is asked for.

    `session` exists so an enqueue can join the caller's transaction. A row
    written and then enqueued outside it leaves the two able to disagree: the
    row says queued and nothing is coming for it. Implementations that have no
    transaction to join accept the argument and ignore it.
    """

    def enqueue(self, request: JobRequest, *, session: Any = None) -> int: ...


@runtime_checkable
class JobStore(Protocol):
    """Where work is kept, claimed and finished.

    Delivery is at least once. A worker can die between finishing the work and
    recording that it finished, and no amount of care here changes that, so a
    handler that must not run twice says so with `max_attempts=1` rather than
    assuming.
    """

    def claim(self, lane: str, worker: str, budget: int) -> Sequence[Lease]: ...

    def heartbeat(self, lease: Lease) -> bool: ...

    def finish(self, lease: Lease, outcome: JobOutcome) -> None: ...

    def release(self, lease: Lease, *, delay_seconds: int = 0) -> None: ...


@runtime_checkable
class JobHandler(Protocol):
    """Something that does one kind of work.

    `kind` is matched against the job's own, so a plugin contributes handlers
    without the core knowing what they are for.
    """

    kind: str

    def run(self, job: Job) -> JobOutcome: ...


@runtime_checkable
class JobMiddleware(Protocol):
    """Something wrapped around a handler.

    Concerns like refusing to overlap, or backing off after repeated refusals
    from a provider, are the same whatever the work is. Composing them here
    keeps them out of both the handler and the table, and lets a plugin add its
    own without a schema change.

    Returning None from `before` lets the work proceed. Returning an outcome
    stops it and is recorded as that outcome, which is how a job that cannot
    take its lock puts itself back in the queue.
    """

    def before(self, job: Job) -> Optional[JobOutcome]: ...

    def after(self, job: Job, outcome: JobOutcome) -> JobOutcome: ...


@runtime_checkable
class JobBatches(Protocol):
    """Progress across several jobs asked for together."""

    def open(self, name: str, total: int, *, tenant: str = "") -> Batch: ...

    def record(self, batch_id: int, *, failed: bool = False) -> Optional[Batch]: ...

    def read_batch(self, batch_id: int) -> Optional[Batch]: ...


@runtime_checkable
class JobWakeup(Protocol):
    """Being told a job arrived rather than asking.

    Optional on purpose. Only some databases can push, and a store that cannot
    is not deficient: polling with a jittered interval is the contract, and
    this only ever shortens the wait.
    """

    def wait(self, lane: str, timeout: float) -> bool: ...


@runtime_checkable
class JobAdmin(Protocol):
    """Reading and steering the queue from outside a worker."""

    def pending(self, lane: str = "", limit: int = 100) -> Sequence[Job]: ...

    def recent(
        self, lane: str = "", kinds: Sequence[str] = (), limit: int = 100
    ) -> Sequence[Job]: ...

    def cancel(self, job_id: int) -> bool: ...

    def retry(self, job_id: int) -> bool: ...
