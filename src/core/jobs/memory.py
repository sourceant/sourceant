"""Keeping jobs in this process only.

This is the reference for how the queue behaves. The database-backed store has
to agree with it, and the tests that pin the behaviour run against both, so
anywhere the two differ is a bug in one of them rather than a detail of the
storage.

It also serves a deployment that has no database, where background work is done
in the request that asked for it and durability is neither offered nor wanted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Optional, Sequence

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryJobStore:
    """Jobs, locks and batches held in this process.

    Everything is guarded by one lock. Contention is not a concern here: a
    process that needed more than one lock's worth of throughput would be one
    that ought to be using the database-backed store.
    """

    def __init__(self, *, lease_seconds: int = 60, cap: int = 3, now=_now) -> None:
        self._lease_seconds = lease_seconds
        self._cap = cap
        self._now = now
        self._lock = RLock()
        self._jobs: dict[int, dict] = {}
        self._locks: dict[str, dict] = {}
        self._batches: dict[int, Batch] = {}
        self._next_id = 1
        self._next_batch = 1

    def enqueue(self, request: JobRequest, *, session: Any = None) -> int:
        """Write a job down and answer with its id.

        `session` is accepted and ignored. Nothing here is transactional, so
        there is no transaction to join, but a caller should not have to know
        which store it is talking to.
        """
        with self._lock:
            slot = request.dedupe_slot
            if slot:
                for row in self._jobs.values():
                    if row["dedupe_slot"] == slot and row["state"] == QUEUED:
                        return row["id"]
            job_id = self._next_id
            self._next_id += 1
            now = self._now()
            self._jobs[job_id] = {
                "id": job_id,
                "lane": request.lane,
                "kind": request.kind,
                "payload": dict(request.payload),
                "state": QUEUED,
                "tenant": request.tenant,
                "tenant_kind": request.tenant_kind,
                "scope_id": request.scope_id,
                "priority": request.priority,
                "attempt": 0,
                "max_attempts": request.max_attempts,
                "deadline_seconds": request.deadline_seconds,
                "exclusive_key": request.exclusive_key,
                "dedupe_slot": slot or f"job:{uuid.uuid4()}",
                "batch_id": request.batch_id,
                "available_at": now + timedelta(seconds=request.delay_seconds),
                "lease_until": None,
                "leased_by": None,
                "claimed_at": None,
                "error": "",
                "created_at": now,
            }
            return job_id

    def claim(self, lane: str, worker: str, budget: int) -> Sequence[Lease]:
        """Take up to `budget` jobs for this worker, fairly.

        A running job whose lease has lapsed is offered alongside the queued
        ones. That is what recovers work from a worker that was killed, and it
        happens on an ordinary poll rather than needing anything scheduled.
        """
        with self._lock:
            now = self._now()
            busy: dict[str, int] = {}
            held: set[str] = set()
            last_served: dict[str, float] = {}
            for row in self._jobs.values():
                if row["state"] != RUNNING or not self._held(row, now):
                    continue
                busy[row["tenant"]] = busy.get(row["tenant"], 0) + 1
                if row["exclusive_key"]:
                    held.add(row["exclusive_key"])
                claimed = row.get("claimed_at")
                if claimed:
                    stamp = claimed.timestamp()
                    if stamp > last_served.get(row["tenant"], float("-inf")):
                        last_served[row["tenant"]] = stamp

            for key, lock in list(self._locks.items()):
                if lock["expires_at"] > now:
                    held.add(key)
                else:
                    del self._locks[key]

            offered = [
                row
                for row in self._jobs.values()
                if row["lane"] == lane and self._takeable(row, now)
            ]
            offered.sort(key=lambda row: (-row["priority"], row["id"]))
            candidates = [
                Candidate(row["id"], row["tenant"], row["exclusive_key"])
                for row in offered
            ]

            chosen = share(
                candidates,
                busy=busy,
                cap=self._cap,
                budget=budget,
                last_served=last_served,
                held=held,
            )

            leases: list[Lease] = []
            for job_id in chosen:
                row = self._jobs[job_id]
                if not self._takeable(row, now):
                    continue
                key = row["exclusive_key"]
                if key and not self._take_lock(key, job_id, now):
                    continue
                row["state"] = RUNNING
                row["attempt"] += 1
                row["leased_by"] = worker
                row["claimed_at"] = now
                row["lease_until"] = now + timedelta(seconds=self._lease_seconds)
                leases.append(
                    Lease(job=self._read(row), worker=worker, until=row["lease_until"])
                )
            return leases

    def heartbeat(self, lease: Lease) -> bool:
        """Extend a claim, and say whether it was still ours to extend.

        A false answer means the lease lapsed and somebody else has the job, so
        the work must stop rather than finish into a row it no longer owns.
        """
        with self._lock:
            row = self._jobs.get(lease.job.id)
            if row is None or row["state"] != RUNNING:
                return False
            if row["leased_by"] != lease.worker:
                return False
            now = self._now()
            row["lease_until"] = now + timedelta(seconds=self._lease_seconds)
            key = row["exclusive_key"]
            if key and key in self._locks and self._locks[key]["job_id"] == row["id"]:
                self._locks[key]["expires_at"] = row["lease_until"]
            return True

    def finish(self, lease: Lease, outcome: JobOutcome) -> None:
        """Record how a job ended, and queue it again where that is wanted."""
        with self._lock:
            row = self._jobs.get(lease.job.id)
            if row is None or row["leased_by"] != lease.worker:
                return
            now = self._now()
            self._drop_lock(row)
            if outcome.succeeded:
                row["state"] = SUCCEEDED
                row["error"] = ""
                self._settle(row, failed=False)
            elif outcome.retry and row["attempt"] < row["max_attempts"]:
                wait = outcome.retry_in
                if wait is None:
                    wait = backoff.after(row["attempt"])
                row["state"] = QUEUED
                row["error"] = outcome.error
                row["available_at"] = now + timedelta(seconds=wait)
            else:
                exhausted = row["attempt"] >= row["max_attempts"]
                row["state"] = DEAD if exhausted else FAILED
                row["error"] = outcome.error
                self._settle(row, failed=True)
            row["lease_until"] = None
            row["leased_by"] = None

    def release(self, lease: Lease, *, delay_seconds: int = 0) -> None:
        """Hand a claim back without counting it as an attempt.

        Used when a job was taken but could not start, which is not the same as
        having tried and failed.
        """
        with self._lock:
            row = self._jobs.get(lease.job.id)
            if row is None or row["leased_by"] != lease.worker:
                return
            self._drop_lock(row)
            row["state"] = QUEUED
            row["attempt"] = max(0, row["attempt"] - 1)
            row["leased_by"] = None
            row["lease_until"] = None
            row["available_at"] = self._now() + timedelta(seconds=delay_seconds)

    def open(self, name: str, total: int, *, tenant: str = "") -> Batch:
        with self._lock:
            batch = Batch(
                id=self._next_batch,
                name=name,
                total=total,
                pending=total,
                tenant=tenant,
            )
            self._batches[batch.id] = batch
            self._next_batch += 1
            return batch

    def record(self, batch_id: int, *, failed: bool = False) -> Optional[Batch]:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return None
            pending = max(0, batch.pending - 1)
            from dataclasses import replace

            updated = replace(
                batch,
                pending=pending,
                failed=batch.failed + (1 if failed else 0),
                finished_at=self._now() if pending == 0 else batch.finished_at,
            )
            self._batches[batch_id] = updated
            return updated

    def read_batch(self, batch_id: int) -> Optional[Batch]:
        with self._lock:
            return self._batches.get(batch_id)

    def read(self, job_id: int) -> Optional[Job]:
        with self._lock:
            row = self._jobs.get(job_id)
            return None if row is None else self._read(row)

    def pending(self, lane: str = "", limit: int = 100) -> Sequence[Job]:
        with self._lock:
            rows = [
                row
                for row in self._jobs.values()
                if row["state"] == QUEUED and (not lane or row["lane"] == lane)
            ]
            rows.sort(key=lambda row: (-row["priority"], row["id"]))
            return [self._read(row) for row in rows[:limit]]

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            row = self._jobs.get(job_id)
            if row is None or row["state"] != QUEUED:
                return False
            row["state"] = CANCELLED
            return True

    def _takeable(self, row: dict, now: datetime) -> bool:
        """Queued and due, or running on a lease that has lapsed."""
        if row["state"] == QUEUED:
            return row["available_at"] <= now
        return row["state"] == RUNNING and not self._held(row, now)

    @staticmethod
    def _held(row: dict, now: datetime) -> bool:
        """Whether a running job's claim is still good.

        A claim that has lapsed is not a job somebody is doing. It is a job
        whose worker is most likely gone, which is why the claim query offers
        it again rather than waiting for anything to notice.
        """
        until = row.get("lease_until")
        return bool(until and until > now)

    def _take_lock(self, key: str, job_id: int, now: datetime) -> bool:
        held = self._locks.get(key)
        if held and held["expires_at"] > now and held["job_id"] != job_id:
            return False
        self._locks[key] = {
            "job_id": job_id,
            "taken_at": now,
            "expires_at": now + timedelta(seconds=self._lease_seconds),
        }
        return True

    def _drop_lock(self, row: dict) -> None:
        key = row["exclusive_key"]
        if key and self._locks.get(key, {}).get("job_id") == row["id"]:
            del self._locks[key]

    def _settle(self, row: dict, *, failed: bool) -> None:
        if row["batch_id"]:
            self.record(row["batch_id"], failed=failed)
        # A finished job releases its dedupe key, so the same work can be
        # asked for again.
        row["dedupe_slot"] = f"job:{uuid.uuid4()}"

    @staticmethod
    def _read(row: dict) -> Job:
        return Job(
            id=row["id"],
            lane=row["lane"],
            kind=row["kind"],
            payload=dict(row["payload"]),
            state=row["state"],
            tenant=row["tenant"],
            tenant_kind=row["tenant_kind"],
            scope_id=row["scope_id"],
            priority=row["priority"],
            attempt=row["attempt"],
            max_attempts=row["max_attempts"],
            deadline_seconds=row["deadline_seconds"],
            exclusive_key=row["exclusive_key"],
            dedupe_slot=row["dedupe_slot"],
            batch_id=row["batch_id"],
            available_at=row["available_at"],
            lease_until=row["lease_until"],
            leased_by=row["leased_by"],
            error=row["error"],
        )


#: One object satisfies both asking for work and taking it, so a test or a
#: single-process deployment needs only this.
InMemoryJobQueue = InMemoryJobStore
