"""A webhook delivery, from the request that brings it to the job that does it."""

import pytest

from src.core.jobs import INTERACTIVE, JobHandler, job_store
from src.core.jobs.worker import Worker
from src.core.services import ServiceRegistry
from src.events.delivery import KIND, Deliveries
from src.models.repository_event import RepositoryEvent
from src.tests.base_test import BaseTestCase


def _failed_with(store, wanted: str) -> bool:
    import sqlalchemy as sa

    from src.core.jobs.sql import job_table

    with store._engine.connect() as connection:
        errors = connection.execute(sa.select(job_table.c.error)).scalars().all()
    return any(wanted in (error or "") for error in errors)


WEBHOOK = {
    "action": "opened",
    "pull_request": {
        "url": "https://api.github.com/repos/sourceant/sourceant/pulls/1",
        "title": "Fix bug",
        "number": 1,
    },
    "repository": {"full_name": "sourceant/sourceant"},
    "sender": {"login": "octocat"},
}


class TestDeliveriesOnTheJobsTable(BaseTestCase):

    @pytest.fixture(autouse=True)
    def queueing_to_the_table(self, monkeypatch):
        monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "database")

    def _deliver(self) -> None:
        response = self.client.post(
            "/api/prs/github-webhook",
            headers={"X-GitHub-Event": "pull_request"},
            json=WEBHOOK,
        )
        assert response.status_code == 201

    def _queued(self):
        waiting = [
            job
            for job in job_store().pending(lane=INTERACTIVE, limit=100)
            if job.kind == KIND
        ]
        assert waiting, "the delivery left nothing to do"
        return waiting[-1]

    def test_a_webhook_leaves_a_job_naming_the_delivery_it_saved(self):
        self._deliver()

        job = self._queued()
        event = RepositoryEvent.get(job.payload["repository_event_id"])
        assert event is not None
        assert event.number == 1
        assert event.repository_full_name == "sourceant/sourceant"

    def test_a_repository_nobody_has_connected_still_takes_its_own_turn(self):
        self._deliver()

        job = self._queued()
        assert job.tenant == "sourceant/sourceant"
        assert job.tenant_kind == "repository"

    def test_a_delivery_is_not_repeated_when_it_fails(self):
        self._deliver()

        assert self._queued().max_attempts == 1

    def test_a_delivery_nothing_wrote_down_is_still_done(self):
        from unittest.mock import patch

        from src.core.plugins import event_hooks

        seen = []
        event_hooks.subscribe_to_events(
            "test_subscriber",
            lambda event_type, data: seen.append(event_type),
            ["pull_request.opened"],
        )
        try:
            with patch(
                "src.controllers.repository_event_controller.STATELESS_MODE", True
            ):
                self._deliver()
        finally:
            event_hooks._event_subscribers.pop("pull_request.opened", None)

        assert seen == ["pull_request.opened"]

    def test_a_delivery_no_subscriber_could_act_on_is_recorded_as_failed(self):
        from src.core.plugins import event_hooks

        def refuse(event_type, data):
            raise RuntimeError("the signing key is a directory")

        event_hooks.subscribe_to_events(
            "test_subscriber", refuse, ["pull_request.opened"]
        )
        try:
            self._deliver()

            services = ServiceRegistry()
            services.contribute(JobHandler, Deliveries(), "sourceant_core")
            worker = Worker(job_store(), INTERACTIVE, services=services)
            worker.work(max_jobs=len(job_store().pending(lane=INTERACTIVE)))
        finally:
            event_hooks._event_subscribers.pop("pull_request.opened", None)

        assert _failed_with(job_store(), "the signing key is a directory")

    def test_a_worker_tells_the_subscribers_what_arrived(self):
        from src.core.plugins import event_hooks

        seen = []
        event_hooks.subscribe_to_events(
            "test_subscriber",
            lambda event_type, data: seen.append(event_type),
            ["pull_request.opened"],
        )
        try:
            self._deliver()

            services = ServiceRegistry()
            services.contribute(JobHandler, Deliveries(), "sourceant_core")
            worker = Worker(job_store(), INTERACTIVE, services=services)
            worker.work(max_jobs=len(job_store().pending(lane=INTERACTIVE)))
        finally:
            event_hooks._event_subscribers.pop("pull_request.opened", None)

        assert "pull_request.opened" in seen


def test_work_that_has_not_moved_yet_still_has_its_redis_queue():
    """A plugin asks for its own background work through this queue, so a mode
    that stopped building it would stop that work rather than move it."""
    import os
    import subprocess
    import sys

    asked = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.events import dispatcher;"
            "print('queue:', dispatcher.q is not None)",
        ],
        env={**os.environ, "QUEUE_MODE": "database"},
        capture_output=True,
        text=True,
    )
    assert "queue: True" in asked.stdout, asked.stderr
