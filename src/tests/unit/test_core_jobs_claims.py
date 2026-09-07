"""What a claim may take, and what happens when one is lost.

Every case here was a way the queue did the same work twice, or stopped doing
it at all. They are checked against the store rather than argued about.
"""

import threading
import time

import sqlalchemy as sa

from src.core.jobs.interfaces import JobHandler
from src.core.jobs.models import (
    DEAD,
    RUNNING,
    BACKGROUND,
    INTERACTIVE,
    JobOutcome,
    JobRequest,
)
from src.core.jobs.sql import SQLJobStore, job_table
from src.core.jobs.sweep import SWEEP_SLOT, Sweeper
from src.core.jobs.worker import LOST_EXIT, Worker
from src.core.services import ServiceRegistry


def store_at(tmp_path, **kwargs) -> SQLJobStore:
    engine = sa.create_engine(f"sqlite:///{tmp_path}/jobs.db")
    return SQLJobStore(engine, create_schema=True, **kwargs)


def abandon(store: SQLJobStore, job_id: int) -> None:
    """Leave a job claimed by a worker that is never coming back."""
    with store._engine.begin() as connection:
        connection.execute(
            sa.update(job_table)
            .where(job_table.c.id == job_id)
            .values(lease_until=sa.text("'1970-01-01 00:00:00'"))
        )


def test_work_asked_for_once_is_not_offered_again_after_a_worker_dies(tmp_path):
    store = store_at(tmp_path)
    job_id = store.enqueue(
        JobRequest(lane=INTERACTIVE, kind="test.once", max_attempts=1)
    )

    assert [lease.job.id for lease in store.claim(INTERACTIVE, "first", 1)] == [job_id]
    abandon(store, job_id)

    assert store.claim(INTERACTIVE, "second", 1) == []
    assert store.read(job_id).attempt == 1


def test_work_asked_for_twice_is_offered_again_after_a_worker_dies(tmp_path):
    store = store_at(tmp_path)
    job_id = store.enqueue(
        JobRequest(lane=INTERACTIVE, kind="test.twice", max_attempts=2)
    )

    store.claim(INTERACTIVE, "first", 1)
    abandon(store, job_id)

    assert [lease.job.id for lease in store.claim(INTERACTIVE, "second", 1)] == [job_id]
    assert store.read(job_id).attempt == 2


def test_work_nobody_may_take_again_stops_saying_it_is_running(tmp_path):
    store = store_at(tmp_path)
    job_id = store.enqueue(
        JobRequest(lane=INTERACTIVE, kind="test.once", max_attempts=1)
    )
    store.claim(INTERACTIVE, "first", 1)
    abandon(store, job_id)

    store.prune(0)

    assert store.read(job_id).state == DEAD


def test_a_backlog_for_one_tenant_does_not_hide_another_tenants_work(tmp_path):
    store = store_at(tmp_path, cap=1, worker_window=5)
    for _ in range(6):
        store.enqueue(
            JobRequest(lane=INTERACTIVE, kind="test.many").for_("busy-tenant")
        )
    waiting = store.enqueue(
        JobRequest(lane=INTERACTIVE, kind="test.one").for_("quiet-tenant")
    )

    store.claim(INTERACTIVE, "first", 1)

    taken = [lease.job.id for lease in store.claim(INTERACTIVE, "second", 1)]
    assert taken == [waiting]


def test_two_tenants_take_turns_rather_than_one_going_twice(tmp_path):
    store = store_at(tmp_path, cap=1)
    for tenant in ("a", "b"):
        for _ in range(2):
            store.enqueue(JobRequest(lane=INTERACTIVE, kind="test.turns").for_(tenant))

    served = []
    for _ in range(3):
        for lease in store.claim(INTERACTIVE, "worker", 1):
            served.append(lease.job.tenant)
            store.finish(lease, JobOutcome.ok())

    assert served == ["a", "b", "a"]


def test_a_tidy_asks_for_the_next_one_while_it_is_still_running(tmp_path):
    store = store_at(tmp_path)
    sweeper = Sweeper(store, every_seconds=0)
    first = sweeper.arrange(store)
    lease = store.claim(BACKGROUND, "worker", 1)[0]
    assert lease.job.id == first

    outcome = sweeper.run(lease.job)
    store.finish(lease, outcome)

    assert outcome.succeeded
    queued = [job for job in store.pending(lane=BACKGROUND) if job.id != first]
    assert len(queued) == 1
    assert queued[0].dedupe_slot == SWEEP_SLOT


def test_a_worker_whose_claim_is_taken_stops_rather_than_carrying_on(tmp_path):
    store = store_at(tmp_path, lease_seconds=1)
    store.enqueue(JobRequest(lane=INTERACTIVE, kind="test.slow", max_attempts=2))

    running = threading.Event()

    class Slow:
        kind = "test.slow"

        def run(self, job):
            running.set()
            time.sleep(10)
            return JobOutcome.ok()

    services = ServiceRegistry()
    services.contribute(JobHandler, Slow(), "test")

    halted = []
    worker = Worker(
        store,
        INTERACTIVE,
        name="first",
        poll_seconds=0.05,
        heartbeat_seconds=0.2,
        services=services,
        halt=halted.append,
    )
    lease = store.claim(INTERACTIVE, "first", 1)[0]
    doing = threading.Thread(target=worker.perform, args=(lease,), daemon=True)
    doing.start()
    assert running.wait(5)

    abandon(store, lease.job.id)
    store.claim(INTERACTIVE, "second", 1)

    doing.join(timeout=15)
    assert halted == [LOST_EXIT]
