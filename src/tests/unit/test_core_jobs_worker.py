"""What a worker does with a job, including when the job misbehaves."""

import pytest

from src.core.jobs.interfaces import JobHandler, JobMiddleware
from src.core.jobs.memory import InMemoryJobStore
from src.core.jobs.models import WITHIN_SECONDS, JobOutcome, JobRequest
from src.core.jobs.worker import DEADLINE_EXIT, Worker
from src.core.services import ServiceRegistry


class Handler:
    def __init__(self, kind="test.work", answer=None, raises=None, blocks=None):
        self.kind = kind
        self._answer = answer or JobOutcome.ok()
        self._raises = raises
        self._blocks = blocks
        self.ran = []

    def run(self, job):
        self.ran.append(job.id)
        if self._raises:
            raise self._raises
        if self._blocks is not None:
            self._blocks.wait()
        return self._answer


@pytest.fixture
def services():
    return ServiceRegistry()


@pytest.fixture
def store():
    return InMemoryJobStore(lease_seconds=60, cap=3)


def _worker(store, services, **over):
    return Worker(store, WITHIN_SECONDS, name="worker-1", services=services, **over)


def _asked(**over):
    asked = {"lane": WITHIN_SECONDS, "kind": "test.work", "tenant": "acme"}
    asked.update(over)
    return JobRequest(**asked)


def test_a_job_reaches_the_handler_that_claims_its_kind(store, services):
    handler = Handler()
    services.contribute(JobHandler, handler, "test")
    job_id = store.enqueue(_asked())

    _worker(store, services).work(max_jobs=1)

    assert handler.ran == [job_id]
    assert store.read(job_id).state == "succeeded"


def test_a_handler_that_raises_is_recorded_rather_than_lost(store, services):
    """A job that vanishes is worse than one that failed, because nothing says
    it needs looking at."""
    services.contribute(JobHandler, Handler(raises=ValueError("nope")), "test")
    job_id = store.enqueue(_asked())

    _worker(store, services).work(max_jobs=1)

    kept = store.read(job_id)
    assert kept.state == "dead"
    assert "nope" in kept.error


def test_work_nothing_here_handles_says_so(store, services):
    """A plugin may be installed on one deployment and not another, so this is
    a fact about this machine rather than about the job."""
    job_id = store.enqueue(_asked(kind="somebody.elses.work"))

    _worker(store, services).work(max_jobs=1)

    assert "no handler" in store.read(job_id).error


def test_middleware_can_hold_a_job_back_before_it_runs(store, services):
    """This is how a provider that has just refused us stops being asked."""

    class Holding:
        def before(self, job):
            return JobOutcome.failed("not now", retry=True, retry_in=30)

        def after(self, job, outcome):
            return outcome

    handler = Handler()
    services.contribute(JobHandler, handler, "test")
    services.contribute(JobMiddleware, Holding(), "test")
    job_id = store.enqueue(_asked(max_attempts=3))

    _worker(store, services).work(max_jobs=1)

    assert handler.ran == []
    assert store.read(job_id).state == "queued"


def test_a_job_that_outruns_its_deadline_takes_the_worker_down_with_it(store, services):
    """The work cannot be stopped from inside this process, so recording it as
    finished while it carried on writing would be a lie. The process goes."""
    import threading

    never = threading.Event()
    services.contribute(JobHandler, Handler(blocks=never), "test")
    job_id = store.enqueue(_asked(deadline_seconds=1))
    halted = []

    _worker(store, services, halt=halted.append).work(max_jobs=1)
    never.set()

    assert halted == [DEADLINE_EXIT]
    assert "went past" in store.read(job_id).error


def test_a_worker_asked_to_stop_does_not_take_more_work(store, services):
    handler = Handler()
    services.contribute(JobHandler, handler, "test")
    store.enqueue(_asked())
    worker = _worker(store, services)
    worker.stop()

    assert worker.work() == 0
    assert handler.ran == []
