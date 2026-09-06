"""Doing the work: claiming a job, keeping the claim alive, and finishing it.

One job at a time. More than one at once is another process, not another
thread, because a worker has to be able to stop a job that has gone on too long
and Python cannot stop a thread. Where the deadline is missed this process
records the job and then exits, so whatever it was running dies with it and the
supervisor starts a fresh one. That is the only honest way to enforce a
deadline in this language.

The lease and the deadline are two different things and the difference matters.
A lease says how long a claim stays good without word from the worker, and is
renewed for as long as the work is alive. A deadline says when the work itself
has gone on too long. Treating them as one gives you either a healthy job
killed at an arbitrary point, or a hung job renewing its claim for ever.
"""

from __future__ import annotations

import os
import signal
import socket
import threading
from time import monotonic
from typing import Optional, Sequence

from src.core.services import ServiceRegistry, service_registry
from src.utils.logger import logger

from .interfaces import JobHandler, JobMiddleware, JobStore
from .models import Job, JobOutcome, Lease

#: Told to the supervisor when a job outran its deadline, so a restart is
#: understood as intended rather than as a crash.
DEADLINE_EXIT = 75


class Worker:
    """Claims work for one lane and does it."""

    def __init__(
        self,
        store: JobStore,
        lane: str,
        *,
        name: str = "",
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 20.0,
        services: ServiceRegistry = service_registry,
        halt=os._exit,
    ) -> None:
        self._store = store
        self._lane = lane
        self._name = name or f"{socket.gethostname()}:{os.getpid()}"
        self._poll = poll_seconds
        self._heartbeat = heartbeat_seconds
        self._services = services
        # Injectable only so a test can watch this happen rather than take the
        # test runner down with it.
        self._halt = halt
        self._stopping = threading.Event()

    @property
    def name(self) -> str:
        return self._name

    def stop(self, *_) -> None:
        """Finish the job in hand and then stop.

        A worker killed mid-job loses nothing, because the lease lapses and the
        job is offered again. Draining is still better: it avoids doing the same
        work twice for no reason.
        """
        logger.info(f"{self._name} will stop once the job in hand is done")
        self._stopping.set()

    def attend(self) -> None:
        """Stop cleanly when asked to, rather than being killed mid-job."""
        for asked in (signal.SIGTERM, signal.SIGINT):
            signal.signal(asked, self.stop)

    def work(self, *, max_jobs: int = 0, max_time: float = 0.0) -> int:
        """Claim and run jobs until told to stop, or until a limit is reached.

        The limits exist so a long-lived process is replaced periodically rather
        than kept until something it leaked matters.
        """
        started = monotonic()
        done = 0
        while not self._stopping.is_set():
            if max_jobs and done >= max_jobs:
                logger.info(f"{self._name} has done {done} jobs and will be replaced")
                break
            if max_time and monotonic() - started >= max_time:
                logger.info(f"{self._name} has run its time and will be replaced")
                break

            leases = self._claim()
            if not leases:
                self._stopping.wait(self._poll)
                continue
            for lease in leases:
                self.perform(lease)
                done += 1
        return done

    def _claim(self) -> Sequence[Lease]:
        try:
            return self._store.claim(self._lane, self._name, 1)
        except Exception:
            logger.warning(f"{self._name} could not claim work", exc_info=True)
            self._stopping.wait(self._poll)
            return []

    def perform(self, lease: Lease) -> JobOutcome:
        """Run one job, keeping its claim alive while it runs."""
        job = lease.job
        beating = _Heartbeat(self._store, lease, self._heartbeat)
        beating.start()
        try:
            outcome = self._guarded(job, lease, beating)
        finally:
            beating.stop()
        try:
            self._store.finish(lease, outcome)
        except Exception:
            logger.warning(f"Could not record how job {job.id} ended", exc_info=True)
        return outcome

    def _guarded(self, job: Job, lease: Lease, beating: "_Heartbeat") -> JobOutcome:
        wrapping = list(self._services.contributions(JobMiddleware))
        for layer in wrapping:
            held = _safely(layer.before, job)
            if held is not None:
                return held

        handler = self._handler(job.kind)
        if handler is None:
            # A handler installed on one deployment may be absent on another,
            # so this is a fact about this machine and not about the job.
            logger.error(f"Nothing here handles {job.kind}, so job {job.id} cannot run")
            return JobOutcome.failed(f"no handler for {job.kind}")

        outcome = self._run(handler, job, lease, beating)
        for layer in reversed(wrapping):
            try:
                outcome = layer.after(job, outcome)
            except Exception:
                logger.warning("A job middleware failed on the way out", exc_info=True)
        return outcome

    def _run(
        self, handler, job: Job, lease: Lease, beating: "_Heartbeat"
    ) -> JobOutcome:
        answer: list = []

        def perform() -> None:
            try:
                answer.append(handler.run(job))
            except Exception as error:
                logger.warning(f"Job {job.id} ({job.kind}) failed", exc_info=True)
                answer.append(JobOutcome.failed(f"{type(error).__name__}: {error}"))

        doing = threading.Thread(target=perform, name=f"job-{job.id}", daemon=True)
        doing.start()
        doing.join(timeout=job.deadline_seconds)

        if doing.is_alive():
            return self._overran(job, lease, beating)
        if not answer:
            return JobOutcome.failed("the job stopped without saying how")
        return answer[0]

    def _overran(self, job: Job, lease: Lease, beating: "_Heartbeat") -> JobOutcome:
        """Record a job that outran its deadline, then take the process down.

        The work is still running and cannot be stopped from here. Leaving it
        would mean a job recorded as finished while it carries on writing, so
        the process goes and takes it with it.
        """
        logger.error(
            f"Job {job.id} ({job.kind}) passed {job.deadline_seconds}s; "
            f"{self._name} is stopping so it cannot carry on"
        )
        beating.stop()
        try:
            self._store.finish(
                lease, JobOutcome.failed(f"went past {job.deadline_seconds}s")
            )
        except Exception:
            logger.warning("Could not record the job that overran", exc_info=True)
        self._halt(DEADLINE_EXIT)
        return JobOutcome.failed(f"went past {job.deadline_seconds}s")

    def _handler(self, kind: str) -> Optional[JobHandler]:
        for handler in self._services.contributions(JobHandler):
            if getattr(handler, "kind", None) == kind:
                return handler
        return None


class _Heartbeat:
    """Renews a claim while its job runs, and notices when it is taken away."""

    def __init__(self, store: JobStore, lease: Lease, every: float) -> None:
        self._store = store
        self._lease = lease
        self._every = every
        self._stopping = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.lost = False

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._beat, name=f"heartbeat-{self._lease.job.id}", daemon=True
        )
        self._thread.start()

    def _beat(self) -> None:
        while not self._stopping.wait(self._every):
            try:
                if not self._store.heartbeat(self._lease):
                    # Somebody else holds the job now, and the work already
                    # running cannot be interrupted from here.
                    self.lost = True
                    logger.warning(
                        f"The claim on job {self._lease.job.id} lapsed and was taken"
                    )
                    return
            except Exception:
                logger.warning("Could not renew a claim", exc_info=True)

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=1)


def _safely(call, job: Job) -> Optional[JobOutcome]:
    try:
        return call(job)
    except Exception:
        logger.warning("A job middleware failed on the way in", exc_info=True)
        return None
