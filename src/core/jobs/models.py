"""A unit of background work, and what is known about it.

Nothing here touches a database or a queue. These are the shapes the rest of
the subsystem passes around, so that the fairness rules and the tests that
cover them can run without either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

# Lanes are named by the latency they promise rather than by what runs in
# them, so picking one answers how long the work may wait.
WITHIN_SECONDS = "within_seconds"
WITHIN_MINUTES = "within_minutes"
WITHIN_HOURS = "within_hours"

LANES: tuple[str, ...] = (WITHIN_SECONDS, WITHIN_MINUTES, WITHIN_HOURS)

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
DEAD = "dead"
CANCELLED = "cancelled"

# What a tenant string stands for. A delivery for a repository nobody has
# connected still has to be fair against other such repositories, so "none" is
# a last resort rather than the usual case.
BY_WORKSPACE = "workspace"
BY_REPOSITORY = "repository"
BY_NOBODY = "none"


@dataclass(frozen=True)
class JobRequest:
    """Work asked for, before anything has been written down.

    `deadline_seconds` is not the lease. A lease says how long a claim stays
    good without word from the worker. A deadline says when the work itself has
    gone on too long. Treat them as one and you get either a healthy job killed
    or a hung job renewing its claim for ever.
    """

    lane: str
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    tenant: str = ""
    tenant_kind: str = BY_NOBODY
    scope_id: Optional[int] = None
    priority: int = 0
    delay_seconds: int = 0
    deadline_seconds: int = 300
    max_attempts: int = 1
    #: A key at most one running job may hold. Where two jobs would tread on the
    #: same files, the second waits rather than both proceeding.
    exclusive_key: Optional[str] = None
    #: A key at most one *queued* job may occupy. Left unset, the job gets one
    #: of its own so it never collides with anything.
    dedupe_slot: Optional[str] = None
    batch_id: Optional[int] = None

    def for_(self, tenant: Optional[str], kind: str = BY_WORKSPACE) -> "JobRequest":
        """The same request, attributed to whoever it is for.

        A tenant that cannot be worked out is not an error. It means this job
        shares a bucket with every other unattributed job, which is worse for
        it than being named but better than being invisible to the share.
        """
        from dataclasses import replace

        if not tenant:
            return replace(self, tenant="", tenant_kind=BY_NOBODY)
        return replace(self, tenant=str(tenant), tenant_kind=kind)


@dataclass(frozen=True)
class Job:
    """A job as it stands now, read back from wherever it is kept."""

    id: int
    lane: str
    kind: str
    payload: Mapping[str, Any]
    state: str
    tenant: str = ""
    tenant_kind: str = BY_NOBODY
    scope_id: Optional[int] = None
    priority: int = 0
    attempt: int = 0
    max_attempts: int = 1
    deadline_seconds: int = 300
    exclusive_key: Optional[str] = None
    dedupe_slot: str = ""
    batch_id: Optional[int] = None
    available_at: Optional[datetime] = None
    lease_until: Optional[datetime] = None
    leased_by: Optional[str] = None
    error: str = ""

    @property
    def exhausted(self) -> bool:
        """Whether another attempt is allowed after this one."""
        return self.attempt >= self.max_attempts


@dataclass(frozen=True)
class Candidate:
    """The little of a queued job that choosing between jobs needs.

    Kept separate from `Job` so a poll can read three columns rather than every
    payload it is not going to run.
    """

    id: int
    tenant: str
    exclusive_key: Optional[str] = None


@dataclass(frozen=True)
class Lease:
    """A claim on a job, good until it is renewed or it lapses."""

    job: Job
    worker: str
    until: datetime


@dataclass(frozen=True)
class JobOutcome:
    """How a job ended, and whether it should be tried again.

    `retry_in` overrides the usual backoff, which is what lets a provider's own
    `Retry-After` be honoured instead of guessed at.
    """

    succeeded: bool
    error: str = ""
    retry: bool = False
    retry_in: Optional[int] = None

    @staticmethod
    def ok() -> "JobOutcome":
        return JobOutcome(succeeded=True)

    @staticmethod
    def failed(error: str, *, retry: bool = False, retry_in: Optional[int] = None):
        return JobOutcome(succeeded=False, error=error, retry=retry, retry_in=retry_in)


@dataclass(frozen=True)
class Batch:
    """Several jobs asked for together, so their progress can be read as one.

    Counting is what this is for. Seven repositories initialised at once should
    read as "two of seven done" rather than as seven unrelated rows, six of
    which look like nothing is happening.
    """

    id: int
    name: str
    total: int = 0
    pending: int = 0
    failed: int = 0
    tenant: str = ""
    cancelled_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def done(self) -> int:
        return self.total - self.pending

    @property
    def finished(self) -> bool:
        return self.pending <= 0
