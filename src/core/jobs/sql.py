"""Keeping jobs in the deployment's own database.

The queue lives in the same database as everything else on purpose. A job and
the row it is about are then written in one transaction, so the two can never
disagree about whether work was asked for, and a job survives anything the
database survives.

Correctness rests on a compare and swap rather than on locking. `FOR UPDATE
SKIP LOCKED` is added where the dialect has it, but only to save wasted work:
two workers that both try to claim the same row still produce exactly one
winner without it, which is what lets the same code run on SQLite.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from src.utils.logger import logger

from . import backoff
from .fairness import share
from .models import (
    CANCELLED,
    DEAD,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    Batch,
    Candidate,
    Job,
    JobOutcome,
    JobRequest,
    Lease,
)

metadata = MetaData()

#: A dialect that can pass over a row another worker is already holding. Where
#: it is missing the claim still works; it just does more work to find that out.
SKIPS_LOCKED = ("postgresql", "mysql")

job_table = Table(
    "jobs",
    metadata,
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("lane", String(64), nullable=False),
    Column("kind", String(255), nullable=False),
    Column("tenant", String(255), nullable=False, default=""),
    Column("tenant_kind", String(32), nullable=False, default=""),
    Column("scope_id", BigInteger, nullable=True),
    Column("payload", Text, nullable=False),
    Column("state", String(16), nullable=False),
    Column("priority", Integer, nullable=False, default=0),
    Column("available_at", DateTime, nullable=False),
    Column("deadline_seconds", Integer, nullable=False, default=300),
    Column("attempt", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=1),
    Column("exclusive_key", String(255), nullable=True),
    # Unique, and holding the job's own id once it is no longer queued, so that
    # "at most one queued job per key" needs no partial index. MySQL has none.
    Column("dedupe_slot", String(255), nullable=False),
    Column("batch_id", BigInteger, nullable=True),
    Column("lease_until", DateTime, nullable=True),
    Column("leased_by", String(255), nullable=True),
    Column("claimed_at", DateTime, nullable=True),
    Column("started_at", DateTime, nullable=True),
    Column("finished_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("error", Text, nullable=False, default=""),
    Index("ix_jobs_ready", "lane", "state", "available_at", "priority", "id"),
    Index("ix_jobs_tenant", "lane", "state", "tenant"),
    Index("ix_jobs_lease", "state", "lease_until"),
    Index("ux_jobs_dedupe", "dedupe_slot", unique=True),
)

# The mutex. A primary key makes taking one an INSERT that either works or
# does not, on every engine, with no dialect branching.
job_lock_table = Table(
    "job_locks",
    metadata,
    Column("key", String(255), primary_key=True),
    Column("job_id", BigInteger, nullable=False),
    Column("taken_at", DateTime, nullable=False),
    # A lock with no expiry is held for ever by a worker that dies holding it.
    Column("expires_at", DateTime, nullable=False),
)

job_batch_table = Table(
    "job_batches",
    metadata,
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("name", String(255), nullable=False),
    Column("tenant", String(255), nullable=False, default=""),
    Column("total", Integer, nullable=False, default=0),
    Column("pending", Integer, nullable=False, default=0),
    Column("failed", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
    Column("cancelled_at", DateTime, nullable=True),
    Column("finished_at", DateTime, nullable=True),
)


def _naive(moment: datetime) -> datetime:
    """The same moment in UTC, without a timezone on it.

    Postgres hands back an aware datetime and SQLite a naive one. Comparing one
    with the other raises, so everything below this line is naive UTC.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _offerable(now: datetime):
    """Work a claim may take: never started, or abandoned with a try to spare.

    A claim that has lapsed is only offered again while the job has an attempt
    left. Without that, work asked for once runs twice whenever the worker
    holding it is killed, which is the one thing `max_attempts=1` is for.
    """
    return or_(
        and_(job_table.c.state == QUEUED, job_table.c.available_at <= now),
        and_(
            job_table.c.state == RUNNING,
            job_table.c.lease_until <= now,
            job_table.c.attempt < job_table.c.max_attempts,
        ),
    )


class SQLJobStore:
    """Jobs kept in a database, claimed by compare and swap."""

    def __init__(
        self,
        engine: Engine,
        *,
        create_schema: bool = False,
        lease_seconds: int = 60,
        cap: int = 3,
        worker_window: int = 200,
        now=None,
    ) -> None:
        self._engine = engine
        # Overridable only so a test can move time on rather than sleep. Left
        # alone, the database is the one clock every worker shares.
        self._now = now
        self._lease_seconds = lease_seconds
        self._cap = cap
        self._window = worker_window
        if create_schema:
            metadata.create_all(engine)

    def _clock(self, connection) -> datetime:
        """Now, according to the database rather than this machine.

        Workers do not share a clock. One running a few seconds fast would
        expire leases that are perfectly healthy, and the job would be run twice
        while the first copy was still going.
        """
        if self._now is not None:
            return _naive(self._now())
        return _naive(
            connection.execute(select(func.current_timestamp(type_=DateTime))).scalar()
        )

    def enqueue(self, request: JobRequest, *, session: Any = None) -> int:
        """Write a job down, in the caller's transaction where there is one.

        Joining the caller's transaction is the point of the `session`
        argument. A row written and committed, and only then enqueued, can end
        up with nothing coming for it if the enqueue fails, which is how a run
        sits saying "queued" for ever.
        """
        if session is not None:
            connection = (
                session.connection() if hasattr(session, "connection") else session
            )
            return self._insert(connection, request)
        with self._engine.begin() as connection:
            return self._insert(connection, request)

    def _insert(self, connection, request: JobRequest) -> int:
        now = self._clock(connection)
        slot = request.dedupe_slot
        values = {
            "lane": request.lane,
            "kind": request.kind,
            "tenant": request.tenant,
            "tenant_kind": request.tenant_kind,
            "scope_id": request.scope_id,
            "payload": json.dumps(dict(request.payload)),
            "state": QUEUED,
            "priority": request.priority,
            "available_at": now + timedelta(seconds=request.delay_seconds),
            "deadline_seconds": request.deadline_seconds,
            "attempt": 0,
            "max_attempts": request.max_attempts,
            "exclusive_key": request.exclusive_key,
            "dedupe_slot": slot or f"job:{uuid.uuid4()}",
            "batch_id": request.batch_id,
            "created_at": now,
            "error": "",
        }
        if not slot:
            return connection.execute(
                job_table.insert().values(**values)
            ).inserted_primary_key[0]

        # A savepoint, so that losing the race for a dedupe key does not take
        # the caller's whole transaction down with it on Postgres.
        try:
            with connection.begin_nested():
                return connection.execute(
                    job_table.insert().values(**values)
                ).inserted_primary_key[0]
        except IntegrityError:
            existing = connection.execute(
                select(job_table.c.id, job_table.c.state).where(
                    job_table.c.dedupe_slot == slot
                )
            ).one_or_none()
            if existing is None:
                raise
            if existing.state == QUEUED:
                return existing.id
            # The key says at most one job waits under it, and this one is no
            # longer waiting. A job asking for its own successor holds its key
            # until it finishes, so leaving it would mean the successor is
            # never asked for at all.
            with connection.begin_nested():
                connection.execute(
                    update(job_table)
                    .where(job_table.c.id == existing.id)
                    .values(dedupe_slot=f"job:{uuid.uuid4()}")
                )
                return connection.execute(
                    job_table.insert().values(**values)
                ).inserted_primary_key[0]

    def claim(self, lane: str, worker: str, budget: int) -> Sequence[Lease]:
        """Take up to `budget` jobs for this worker, fairly.

        Work whose lease has lapsed is offered next to work that has never run.
        That is what recovers a job from a worker that was killed, and it
        happens here rather than in anything scheduled, so the recovery works on
        a deployment that never runs the sweep at all.
        """
        if budget <= 0:
            return []
        with self._engine.begin() as connection:
            now = self._clock(connection)
            busy, held, last_served = self._census(connection, lane, now)
            connection.execute(
                delete(job_lock_table).where(job_lock_table.c.expires_at <= now)
            )
            held |= self._locked(connection, now)
            spent = [who for who, count in busy.items() if count >= self._cap]
            candidates = self._candidates(connection, lane, now, spent)

        chosen = share(
            candidates,
            busy=busy,
            cap=self._cap,
            budget=budget,
            last_served=last_served,
            held=held,
        )
        return [
            lease
            for job_id in chosen
            if (lease := self._take(job_id, worker)) is not None
        ]

    def _census(self, connection, lane: str, now: datetime):
        """What is running for whom, and when each tenant last had a turn."""
        rows = connection.execute(
            select(
                job_table.c.tenant,
                func.count(job_table.c.id),
                func.max(job_table.c.claimed_at),
            )
            .where(
                and_(
                    job_table.c.lane == lane,
                    job_table.c.state == RUNNING,
                    job_table.c.lease_until > now,
                )
            )
            .group_by(job_table.c.tenant)
        ).all()
        busy = {tenant: count for tenant, count, _ in rows}
        last_served = self._turns(connection, lane, now)
        running_keys = connection.execute(
            select(job_table.c.exclusive_key).where(
                and_(
                    job_table.c.state == RUNNING,
                    job_table.c.lease_until > now,
                    job_table.c.exclusive_key.is_not(None),
                )
            )
        ).scalars()
        return busy, set(running_keys), last_served

    def _turns(self, connection, lane: str, now: datetime) -> dict:
        """When each tenant last had a turn, whether or not it still holds one.

        Reading this from what is running would forget a tenant the moment its
        job ended, and a tenant that has just been served would then sort as
        one that never has.
        """
        recent = now - timedelta(days=1)
        rows = connection.execute(
            select(job_table.c.tenant, func.max(job_table.c.claimed_at))
            .where(
                and_(
                    job_table.c.lane == lane,
                    job_table.c.claimed_at.is_not(None),
                    job_table.c.claimed_at > recent,
                )
            )
            .group_by(job_table.c.tenant)
        ).all()
        return {
            tenant: _naive(claimed).timestamp()
            for tenant, claimed in rows
            if claimed is not None
        }

    def _locked(self, connection, now: datetime) -> set:
        return set(
            connection.execute(
                select(job_lock_table.c.key).where(job_lock_table.c.expires_at > now)
            ).scalars()
        )

    def _candidates(
        self, connection, lane: str, now: datetime, spent: Sequence[str] = ()
    ) -> list:
        """Queued and due, or running on a claim that has lapsed.

        A tenant already at its cap is left out rather than filtered later. A
        window filled with work nobody may start yet hides everybody else's,
        and the worker takes nothing while another tenant waits.
        """
        query = (
            select(job_table.c.id, job_table.c.tenant, job_table.c.exclusive_key)
            .where(
                and_(
                    job_table.c.lane == lane,
                    _offerable(now),
                )
            )
            .order_by(job_table.c.priority.desc(), job_table.c.id)
            .limit(self._window)
        )
        if spent:
            query = query.where(job_table.c.tenant.notin_(list(spent)))
        if self._engine.dialect.name in SKIPS_LOCKED:
            query = query.with_for_update(skip_locked=True)
        return [
            Candidate(id=row.id, tenant=row.tenant, exclusive_key=row.exclusive_key)
            for row in connection.execute(query).all()
        ]

    def _take(self, job_id: int, worker: str) -> Optional[Lease]:
        """Claim one job, and its lock where it needs one.

        The lock is taken before the claim so that failing to get it costs
        nothing: there is no claim yet to hand back.
        """
        with self._engine.begin() as connection:
            now = self._clock(connection)
            until = now + timedelta(seconds=self._lease_seconds)
            row = connection.execute(
                select(job_table).where(job_table.c.id == job_id)
            ).one_or_none()
            if row is None:
                return None
            if row.exclusive_key and not self._lock(
                connection, row.exclusive_key, job_id, now, until
            ):
                return None

            claimed = connection.execute(
                update(job_table)
                .where(and_(job_table.c.id == job_id, _offerable(now)))
                .values(
                    state=RUNNING,
                    leased_by=worker,
                    lease_until=until,
                    attempt=job_table.c.attempt + 1,
                    claimed_at=now,
                    started_at=now,
                )
            ).rowcount
            if claimed != 1:
                # Somebody else won it. Give back the lock we just took, or the
                # key stays held by a job we are not running.
                if row.exclusive_key:
                    connection.execute(
                        delete(job_lock_table).where(
                            and_(
                                job_lock_table.c.key == row.exclusive_key,
                                job_lock_table.c.job_id == job_id,
                            )
                        )
                    )
                return None

            fresh = connection.execute(
                select(job_table).where(job_table.c.id == job_id)
            ).one()
            return Lease(job=_as_job(fresh), worker=worker, until=until)

    def _lock(self, connection, key: str, job_id: int, now, until) -> bool:
        connection.execute(
            delete(job_lock_table).where(
                and_(job_lock_table.c.key == key, job_lock_table.c.expires_at <= now)
            )
        )
        try:
            with connection.begin_nested():
                connection.execute(
                    job_lock_table.insert().values(
                        key=key, job_id=job_id, taken_at=now, expires_at=until
                    )
                )
            return True
        except IntegrityError:
            return False

    def heartbeat(self, lease: Lease) -> bool:
        """Extend a claim, and say whether it was still ours to extend."""
        with self._engine.begin() as connection:
            now = self._clock(connection)
            until = now + timedelta(seconds=self._lease_seconds)
            kept = connection.execute(
                update(job_table)
                .where(
                    and_(
                        job_table.c.id == lease.job.id,
                        job_table.c.state == RUNNING,
                        job_table.c.leased_by == lease.worker,
                        job_table.c.lease_until > now,
                    )
                )
                .values(lease_until=until)
            ).rowcount
            if kept != 1:
                return False
            if lease.job.exclusive_key:
                connection.execute(
                    update(job_lock_table)
                    .where(
                        and_(
                            job_lock_table.c.key == lease.job.exclusive_key,
                            job_lock_table.c.job_id == lease.job.id,
                        )
                    )
                    .values(expires_at=until)
                )
            return True

    def finish(self, lease: Lease, outcome: JobOutcome) -> None:
        """Record how a job ended, and queue it again where that is wanted."""
        with self._engine.begin() as connection:
            now = self._clock(connection)
            row = connection.execute(
                select(job_table).where(job_table.c.id == lease.job.id)
            ).one_or_none()
            if row is None or row.leased_by != lease.worker:
                return
            self._unlock(connection, row)

            if outcome.succeeded:
                values = {
                    "state": SUCCEEDED,
                    "error": "",
                    "finished_at": now,
                    "dedupe_slot": f"job:{uuid.uuid4()}",
                }
                self._settle(connection, row, now, failed=False)
            elif outcome.retry and row.attempt < row.max_attempts:
                wait = outcome.retry_in
                if wait is None:
                    wait = backoff.after(row.attempt)
                values = {
                    "state": QUEUED,
                    "error": outcome.error,
                    "available_at": now + timedelta(seconds=wait),
                }
            else:
                values = {
                    "state": DEAD if row.attempt >= row.max_attempts else FAILED,
                    "error": outcome.error,
                    "finished_at": now,
                    "dedupe_slot": f"job:{uuid.uuid4()}",
                }
                self._settle(connection, row, now, failed=True)

            values.update({"lease_until": None, "leased_by": None})
            connection.execute(
                update(job_table).where(job_table.c.id == row.id).values(**values)
            )

    def release(self, lease: Lease, *, delay_seconds: int = 0) -> None:
        """Hand a claim back without counting it as an attempt."""
        with self._engine.begin() as connection:
            now = self._clock(connection)
            row = connection.execute(
                select(job_table).where(job_table.c.id == lease.job.id)
            ).one_or_none()
            if row is None or row.leased_by != lease.worker:
                return
            self._unlock(connection, row)
            connection.execute(
                update(job_table)
                .where(job_table.c.id == row.id)
                .values(
                    state=QUEUED,
                    attempt=max(0, row.attempt - 1),
                    leased_by=None,
                    lease_until=None,
                    available_at=now + timedelta(seconds=delay_seconds),
                )
            )

    def _unlock(self, connection, row) -> None:
        if row.exclusive_key:
            connection.execute(
                delete(job_lock_table).where(
                    and_(
                        job_lock_table.c.key == row.exclusive_key,
                        job_lock_table.c.job_id == row.id,
                    )
                )
            )

    def open(self, name: str, total: int, *, tenant: str = "") -> Batch:
        with self._engine.begin() as connection:
            now = self._clock(connection)
            batch_id = connection.execute(
                job_batch_table.insert().values(
                    name=name,
                    tenant=tenant,
                    total=total,
                    pending=total,
                    failed=0,
                    created_at=now,
                )
            ).inserted_primary_key[0]
        return Batch(id=batch_id, name=name, total=total, pending=total, tenant=tenant)

    def record(self, batch_id: int, *, failed: bool = False) -> Optional[Batch]:
        with self._engine.begin() as connection:
            self._settle_batch(connection, batch_id, self._clock(connection), failed)
        return self.read_batch(batch_id)

    def _settle(self, connection, row, now, *, failed: bool) -> None:
        if row.batch_id:
            self._settle_batch(connection, row.batch_id, now, failed)

    def _settle_batch(self, connection, batch_id: int, now, failed: bool) -> None:
        connection.execute(
            update(job_batch_table)
            .where(
                and_(job_batch_table.c.id == batch_id, job_batch_table.c.pending > 0)
            )
            .values(
                pending=job_batch_table.c.pending - 1,
                failed=job_batch_table.c.failed + (1 if failed else 0),
            )
        )
        connection.execute(
            update(job_batch_table)
            .where(
                and_(
                    job_batch_table.c.id == batch_id,
                    job_batch_table.c.pending <= 0,
                    job_batch_table.c.finished_at.is_(None),
                )
            )
            .values(finished_at=now)
        )

    def read_batch(self, batch_id: int) -> Optional[Batch]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(job_batch_table).where(job_batch_table.c.id == batch_id)
            ).one_or_none()
        if row is None:
            return None
        return Batch(
            id=row.id,
            name=row.name,
            total=row.total,
            pending=row.pending,
            failed=row.failed,
            tenant=row.tenant,
            cancelled_at=row.cancelled_at,
            finished_at=row.finished_at,
        )

    def read(self, job_id: int) -> Optional[Job]:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(job_table).where(job_table.c.id == job_id)
            ).one_or_none()
        return None if row is None else _as_job(row)

    def pending(self, lane: str = "", limit: int = 100) -> Sequence[Job]:
        query = select(job_table).where(job_table.c.state == QUEUED)
        if lane:
            query = query.where(job_table.c.lane == lane)
        query = query.order_by(job_table.c.priority.desc(), job_table.c.id).limit(limit)
        with self._engine.connect() as connection:
            return [_as_job(row) for row in connection.execute(query).all()]

    def prune(self, keep_finished_for_days: int = 14) -> int:
        """Clear away jobs finished long enough ago to be of no interest.

        Zero keeps them for ever, which is what the setting offers and also how
        this table becomes the largest one in the database.
        """
        with self._engine.begin() as connection:
            now = self._clock(connection)
            cleared = 0
            if keep_finished_for_days > 0:
                cutoff = now - timedelta(days=keep_finished_for_days)
                cleared = connection.execute(
                    delete(job_table).where(
                        and_(
                            job_table.c.state.in_((SUCCEEDED, FAILED, DEAD, CANCELLED)),
                            job_table.c.finished_at.is_not(None),
                            job_table.c.finished_at < cutoff,
                        )
                    )
                ).rowcount
            self._bury(connection, now)
            # A lock outliving its job would hold work back for ever, and the
            # claim only passes over ones that have not expired yet.
            connection.execute(
                delete(job_lock_table).where(job_lock_table.c.expires_at <= now)
            )
            return cleared or 0

    def _bury(self, connection, now: datetime) -> None:
        """Settle work whose worker went away with no attempt left.

        The claim will not offer it again, so without this it stays running for
        ever and its dedupe key stays taken.
        """
        abandoned = connection.execute(
            select(job_table).where(
                and_(
                    job_table.c.state == RUNNING,
                    job_table.c.lease_until <= now,
                    job_table.c.attempt >= job_table.c.max_attempts,
                )
            )
        ).all()
        for row in abandoned:
            self._unlock(connection, row)
            connection.execute(
                update(job_table)
                .where(job_table.c.id == row.id)
                .values(
                    state=DEAD,
                    error="the worker holding this went away",
                    finished_at=now,
                    lease_until=None,
                    leased_by=None,
                    dedupe_slot=f"job:{uuid.uuid4()}",
                )
            )
            self._settle(connection, row, now, failed=True)

    def cancel(self, job_id: int) -> bool:
        with self._engine.begin() as connection:
            now = self._clock(connection)
            return (
                connection.execute(
                    update(job_table)
                    .where(and_(job_table.c.id == job_id, job_table.c.state == QUEUED))
                    .values(
                        state=CANCELLED,
                        # Cancelled is an ending like any other: it releases the
                        # key so the same work can be asked for again, and it is
                        # dated so that tidying up eventually reaches it.
                        finished_at=now,
                        dedupe_slot=f"job:{uuid.uuid4()}",
                    )
                ).rowcount
                == 1
            )


def _as_job(row) -> Job:
    try:
        payload = json.loads(row.payload)
    except (TypeError, ValueError):
        logger.warning(f"Job {row.id} has a payload that is not readable")
        payload = {}
    return Job(
        id=row.id,
        lane=row.lane,
        kind=row.kind,
        payload=payload,
        state=row.state,
        tenant=row.tenant,
        tenant_kind=row.tenant_kind,
        scope_id=row.scope_id,
        priority=row.priority,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        deadline_seconds=row.deadline_seconds,
        exclusive_key=row.exclusive_key,
        dedupe_slot=row.dedupe_slot,
        batch_id=row.batch_id,
        available_at=row.available_at,
        lease_until=row.lease_until,
        leased_by=row.leased_by,
        error=row.error or "",
    )
