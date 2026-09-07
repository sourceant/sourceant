"""How the queue behaves, whatever is keeping it.

Every case here is written against the interface rather than against a store,
so the database-backed one can be held to exactly the same behaviour. Where the
two would differ, one of them is wrong.
"""

from datetime import datetime, timedelta, timezone

import pytest

import os

import sqlalchemy as sa

from src.core.jobs.memory import InMemoryJobStore
from src.core.jobs.models import INTERACTIVE, DEAD, QUEUED, JobOutcome, JobRequest
from src.core.jobs.sql import SQLJobStore, metadata


class Clock:
    """A clock the test moves, so waiting is asserted rather than slept through."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def ahead(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


# A real server is used where one is offered, because SQLite never reaches the
# paths that matter most: `FOR UPDATE SKIP LOCKED`, and a failed insert rolled
# back to a savepoint rather than taking the transaction with it.
SERVERS = {
    "mysql": os.environ.get("JOBS_TEST_MYSQL_URL", ""),
    "postgres": os.environ.get("JOBS_TEST_POSTGRES_URL", ""),
}
STORES = ["memory", "sqlite"] + [name for name, url in SERVERS.items() if url]


@pytest.fixture(params=STORES)
def store(request, clock, tmp_path):
    """Every store, held to the same behaviour.

    The database-backed one is what production uses and the in-process one is
    what tests and a stateless deployment use, so a difference between them is
    a bug rather than a detail of storage.
    """
    if request.param == "memory":
        return InMemoryJobStore(lease_seconds=60, cap=3, now=clock)
    if request.param == "sqlite":
        engine = sa.create_engine(f"sqlite:///{tmp_path}/jobs.db")
    else:
        engine = sa.create_engine(SERVERS[request.param])
        metadata.drop_all(engine)
    return SQLJobStore(engine, create_schema=True, lease_seconds=60, cap=3, now=clock)


def _asked(**over) -> JobRequest:
    asked = {"lane": INTERACTIVE, "kind": "test.work", "tenant": "acme"}
    asked.update(over)
    return JobRequest(**asked)


def test_a_job_is_claimed_run_and_finished(store):
    store.enqueue(_asked())

    (lease,) = store.claim(INTERACTIVE, "worker-1", 5)
    store.finish(lease, JobOutcome.ok())

    assert store.read(lease.job.id).state == "succeeded"


def test_work_from_a_killed_worker_is_offered_again_once_its_lease_lapses(store, clock):
    """This is the whole point. A worker that is killed writes nothing, so
    nothing running inside it can be what recovers the job."""
    job_id = store.enqueue(_asked(max_attempts=2))
    (first,) = store.claim(INTERACTIVE, "worker-1", 5)
    assert first.job.id == job_id

    assert store.claim(INTERACTIVE, "worker-2", 5) == []

    clock.ahead(61)
    (second,) = store.claim(INTERACTIVE, "worker-2", 5)

    assert second.job.id == job_id
    assert second.job.attempt == 2


def test_a_worker_whose_lease_was_taken_away_is_told_so(store, clock):
    """It must stop rather than finish into a row somebody else now owns."""
    store.enqueue(_asked())
    (first,) = store.claim(INTERACTIVE, "worker-1", 5)
    assert store.heartbeat(first) is True

    clock.ahead(61)
    store.claim(INTERACTIVE, "worker-2", 5)

    assert store.heartbeat(first) is False


def test_asking_twice_for_the_same_work_queues_it_once(store):
    """A page that refreshes advisories on every view would otherwise pile up
    thousands of identical durable jobs."""
    first = store.enqueue(_asked(dedupe_slot="refresh:7"))
    second = store.enqueue(_asked(dedupe_slot="refresh:7"))

    assert first == second
    assert len(store.pending(INTERACTIVE)) == 1


def test_the_same_work_can_be_asked_for_again_once_it_has_finished(store):
    """Otherwise "not twice at once" quietly becomes "never again"."""
    first = store.enqueue(_asked(dedupe_slot="refresh:7"))
    (lease,) = store.claim(INTERACTIVE, "worker-1", 5)
    store.finish(lease, JobOutcome.ok())

    second = store.enqueue(_asked(dedupe_slot="refresh:7"))

    assert second != first


def test_two_jobs_needing_the_same_files_do_not_run_at_once(store):
    """Two initializations of one repository delete each other's checkout."""
    store.enqueue(_asked(exclusive_key="working-area:5"))
    store.enqueue(_asked(exclusive_key="working-area:5"))

    claimed = store.claim(INTERACTIVE, "worker-1", 5)

    assert len(claimed) == 1


def test_a_lock_held_by_a_worker_that_died_does_not_block_for_ever(store, clock):
    """A mutex without an expiry replaces one outage with a longer one."""
    store.enqueue(_asked(exclusive_key="working-area:5"))
    store.enqueue(_asked(exclusive_key="working-area:5"))
    store.claim(INTERACTIVE, "worker-1", 1)

    clock.ahead(61)
    claimed = store.claim(INTERACTIVE, "worker-2", 5)

    assert len(claimed) == 1


def test_work_that_must_not_run_twice_is_not_retried(store):
    """Initialization writes proposals as it goes, so a second attempt at a
    half-finished run proposes the same things again."""
    store.enqueue(_asked(max_attempts=1))
    (lease,) = store.claim(INTERACTIVE, "worker-1", 5)

    store.finish(lease, JobOutcome.failed("no", retry=True))

    assert store.read(lease.job.id).state == DEAD


def test_a_provider_asking_us_to_wait_is_waited_for(store, clock):
    """A 429 carries how long to wait. Guessing instead is how a rate limit
    becomes a failure."""
    store.enqueue(_asked(max_attempts=3))
    (lease,) = store.claim(INTERACTIVE, "worker-1", 5)

    store.finish(lease, JobOutcome.failed("429", retry=True, retry_in=120))

    assert store.read(lease.job.id).state == QUEUED
    assert store.claim(INTERACTIVE, "worker-1", 5) == []
    clock.ahead(121)
    assert len(store.claim(INTERACTIVE, "worker-1", 5)) == 1


def test_handing_a_claim_back_does_not_count_as_an_attempt(store):
    """A job taken but never started has not been tried."""
    store.enqueue(_asked(max_attempts=1))
    (lease,) = store.claim(INTERACTIVE, "worker-1", 5)

    store.release(lease)

    assert store.read(lease.job.id).attempt == 0
    assert store.read(lease.job.id).state == QUEUED


def test_a_batch_counts_down_as_its_jobs_finish(store):
    """Seven repositories at once should read as progress, not as six rows
    where nothing appears to be happening."""
    batch = store.open("initialize", total=2, tenant="acme")
    store.enqueue(_asked(batch_id=batch.id))
    store.enqueue(_asked(batch_id=batch.id))

    for lease in store.claim(INTERACTIVE, "worker-1", 5):
        store.finish(lease, JobOutcome.ok())

    done = store.read_batch(batch.id)
    assert done.pending == 0
    assert done.finished_at is not None


def test_a_delayed_job_is_not_offered_before_it_is_due(store, clock):
    store.enqueue(_asked(delay_seconds=30))

    assert store.claim(INTERACTIVE, "worker-1", 5) == []
    clock.ahead(31)
    assert len(store.claim(INTERACTIVE, "worker-1", 5)) == 1


def test_finished_work_is_cleared_away_but_work_still_waiting_is_not(store, clock):
    """Nothing clears this table on its own, so without pruning it grows for
    as long as the deployment runs."""
    done = store.enqueue(_asked())
    (lease,) = store.claim(INTERACTIVE, "worker-1", 5)
    store.finish(lease, JobOutcome.ok())
    waiting = store.enqueue(_asked(dedupe_slot="still-wanted"))

    clock.ahead(15 * 24 * 60 * 60)
    cleared = store.prune(keep_finished_for_days=14)

    assert cleared == 1
    assert store.read(done) is None
    assert store.read(waiting).state == QUEUED


def test_keeping_them_for_ever_is_a_choice_that_is_honoured(store, clock):
    store.enqueue(_asked())
    (lease,) = store.claim(INTERACTIVE, "worker-1", 5)
    store.finish(lease, JobOutcome.ok())

    clock.ahead(400 * 24 * 60 * 60)

    assert store.prune(keep_finished_for_days=0) == 0
    assert store.read(lease.job.id) is not None


def test_cancelling_gives_the_key_back_and_can_be_cleared_away(store, clock):
    """Cancelled is an ending. Held open, the key blocks the same work from ever
    being asked for again, and nothing tidying up would ever reach the row."""
    first = store.enqueue(_asked(dedupe_slot="refresh:9"))

    assert store.cancel(first) is True

    second = store.enqueue(_asked(dedupe_slot="refresh:9"))
    assert second != first

    clock.ahead(15 * 24 * 60 * 60)
    assert store.prune(keep_finished_for_days=14) == 1
