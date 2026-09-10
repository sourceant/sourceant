"""Reading background work, and only your own."""

import os
import time

import jwt

from src.core.jobs import BATCH, INTERACTIVE, JobOutcome, JobRequest, job_store
from src.tests.base_test import BaseTestCase

OURS = "workspace-ours"
THEIRS = "workspace-theirs"
# Its own, so that a cap on how much one workspace runs at once cannot have
# this batch waiting behind work another test left queued.
COUNTING = "workspace-counting"


def _as(workspace: str) -> dict:
    token = jwt.encode(
        {
            "sub": "1",
            "username": "octocat",
            "scope": {"workspace_id": workspace},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class TestReadingWork(BaseTestCase):

    def test_a_job_says_what_became_of_it(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")
        job_id = job_store().enqueue(
            JobRequest(lane=INTERACTIVE, kind="test.work").for_(OURS)
        )

        read = self.client.get(f"/api/jobs/{job_id}", headers=_as(OURS))

        assert read.status_code == 200
        assert read.json()["data"]["kind"] == "test.work"
        assert read.json()["data"]["state"] == "queued"

    def test_work_belonging_to_somebody_else_is_not_there(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")
        job_id = job_store().enqueue(
            JobRequest(lane=INTERACTIVE, kind="test.private").for_(THEIRS)
        )

        read = self.client.get(f"/api/jobs/{job_id}", headers=_as(OURS))

        assert read.status_code == 404

    def test_the_listing_holds_only_your_own(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")
        job_store().enqueue(JobRequest(lane=BATCH, kind="test.mine").for_(OURS))
        job_store().enqueue(JobRequest(lane=BATCH, kind="test.not-mine").for_(THEIRS))

        listed = self.client.get("/api/jobs?lane=batch", headers=_as(OURS))

        kinds = {job["kind"] for job in listed.json()["data"]}
        assert "test.mine" in kinds
        assert "test.not-mine" not in kinds

    def test_a_lane_nobody_has_is_refused(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")

        listed = self.client.get("/api/jobs?lane=whenever", headers=_as(OURS))

        assert listed.status_code == 400

    def test_waiting_work_is_filtered_by_kind_like_the_rest(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")
        job_store().enqueue(JobRequest(lane=BATCH, kind="test.wanted").for_(OURS))
        job_store().enqueue(JobRequest(lane=BATCH, kind="test.other").for_(OURS))

        listed = self.client.get(
            "/api/jobs?lane=batch&waiting_only=true&kind=test.wanted",
            headers=_as(OURS),
        )

        assert listed.status_code == 200
        assert {job["kind"] for job in listed.json()["data"]} == {"test.wanted"}


class TestReadingABatch(BaseTestCase):

    def test_a_batch_counts_down_as_its_work_finishes(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")
        store = job_store()
        batch = store.open("reading three repositories", 3, tenant=COUNTING)
        for _ in range(3):
            store.enqueue(
                JobRequest(lane=INTERACTIVE, kind="test.part", batch_id=batch.id).for_(
                    COUNTING
                )
            )

        read = self.client.get(f"/api/jobs/batches/{batch.id}", headers=_as(COUNTING))
        assert read.json()["data"]["total"] == 3
        assert read.json()["data"]["done"] == 0
        assert read.json()["data"]["finished"] is False

        for _ in range(20):
            leases = store.claim(INTERACTIVE, "worker", 3)
            if not leases:
                break
            for lease in leases:
                store.finish(lease, JobOutcome.ok())
            if store.read_batch(batch.id).finished:
                break

        read = self.client.get(f"/api/jobs/batches/{batch.id}", headers=_as(COUNTING))
        assert read.json()["data"]["done"] == 3
        assert read.json()["data"]["finished"] is True

    def test_somebody_elses_batch_is_not_there(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")
        batch = job_store().open("theirs", 1, tenant=THEIRS)

        read = self.client.get(f"/api/jobs/batches/{batch.id}", headers=_as(OURS))

        assert read.status_code == 404

    def test_a_batch_nobody_opened_is_not_there(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "jobs-api-secret")

        read = self.client.get("/api/jobs/batches/999999", headers=_as(OURS))

        assert read.status_code == 404
