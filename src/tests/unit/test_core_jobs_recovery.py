"""Work outliving the worker that was doing it.

A worker killed outright writes nothing, so recovery cannot be anything that
runs inside it. The kill here is real for that reason: a second worker picks
the job up because the claim lapsed, with nothing scheduled and nothing swept.
"""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import sqlalchemy as sa

from src.core.jobs.models import WITHIN_SECONDS, JobRequest
from src.core.jobs.sql import SQLJobStore

WORKER = textwrap.dedent("""
    import sys, time
    import sqlalchemy as sa
    from src.core.jobs.sql import SQLJobStore
    from src.core.jobs.worker import Worker
    from src.core.jobs.interfaces import JobHandler
    from src.core.services import ServiceRegistry
    from src.core.jobs.models import WITHIN_SECONDS, JobOutcome

    class Forever:
        kind = "test.long"
        def run(self, job):
            while True:
                time.sleep(0.05)

    services = ServiceRegistry()
    services.contribute(JobHandler, Forever(), "test")
    store = SQLJobStore(sa.create_engine(sys.argv[1]), lease_seconds=2)
    Worker(store, WITHIN_SECONDS, name="doomed", poll_seconds=0.1,
           heartbeat_seconds=0.5, services=services).work()
    """)


def test_a_job_survives_the_worker_being_killed_outright(tmp_path):
    url = f"sqlite:///{tmp_path}/jobs.db"
    store = SQLJobStore(sa.create_engine(url), create_schema=True, lease_seconds=2)
    job_id = store.enqueue(
        JobRequest(
            lane=WITHIN_SECONDS,
            kind="test.long",
            tenant="acme",
            max_attempts=3,
            deadline_seconds=600,
        )
    )

    script = tmp_path / "worker.py"
    script.write_text(WORKER)
    root = Path(__file__).resolve().parents[3]
    # A fresh interpreter takes its first import path from the script, which is
    # in a temporary directory, so it is told where the package is.
    said = tmp_path / "worker.err"
    doomed = subprocess.Popen(
        [sys.executable, str(script), url],
        cwd=str(root),
        env={**os.environ, "PYTHONPATH": str(root)},
        stderr=said.open("w"),
    )
    try:
        _until(
            lambda: store.read(job_id).state == "running",
            "the job to start",
            said=said,
        )
        assert store.read(job_id).leased_by == "doomed"

        # Not a signal the worker can catch, tidy up after, or write anything
        # in response to. Exactly what an out-of-memory kill looks like.
        doomed.send_signal(signal.SIGKILL)
        doomed.wait(timeout=10)

        _until(
            lambda: store.claim(WITHIN_SECONDS, "worker-2", 1) != [],
            "the lapsed claim to be offered again",
            wait=6,
        )
    finally:
        if doomed.poll() is None:
            doomed.kill()

    taken = store.read(job_id)
    assert taken.state == "running"
    assert taken.leased_by == "worker-2"
    assert taken.attempt == 2


def _until(ready, what: str, wait: float = 10.0, said: Path = None) -> None:
    until = time.monotonic() + wait
    while time.monotonic() < until:
        if ready():
            return
        time.sleep(0.1)
    complaint = said.read_text().strip() if said and said.exists() else ""
    raise AssertionError(
        f"Waited {wait}s for {what} and it did not happen. {complaint}".strip()
    )
