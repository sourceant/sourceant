"""Asking for a system's connections, over the same HTTP a caller uses.

The point of every case here is that the asking returns before the reading
does, and that a reading which failed never comes back looking like a system
with nothing joining it.
"""

import os
import time
import uuid

import jwt
import pytest
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes.topology import get_topology_repository
from src.core.jobs import job_store
from src.core.jobs.models import DEAD
from src.core.topology import SQLTopologyRepository
from src.core.topology.discovery import MANIFESTS, READING
from src.tests.base_test import BaseTestCase

TEST_JWT_SECRET = "topology-discover-api-test-secret"


def _token(workspace_id: str) -> str:
    return jwt.encode(
        {
            "sub": "1",
            "username": "octocat",
            "github_token": "gh-token",
            "scope": {"workspace_id": workspace_id, "repository_ids": []},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


class Model:
    """A model that is configured and never asked, because nothing reads here."""

    def ask_with_tools(self, messages, tools, *, purpose="", require=False):
        raise AssertionError("a discovery must not read in the request that asks")

    def missing_credentials(self):
        return []


class TestAskingForADiscovery(BaseTestCase):
    @pytest.fixture(autouse=True)
    def workspace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
        self.workspace_id = uuid.uuid4().hex
        self.headers = {"Authorization": f"Bearer {_token(self.workspace_id)}"}
        self.repository = SQLTopologyRepository(
            create_engine(f"sqlite:///{tmp_path / 'topology.db'}"),
            create_schema=True,
        )
        app.dependency_overrides[get_topology_repository] = lambda: self.repository
        monkeypatch.setattr(
            "src.api.routes.requirements.connected_names",
            lambda user: {"acme/checkout", "acme/billing"},
        )
        # Nothing here reaches a forge. Where each repository stands is what a
        # discovery asks for before it queues anything.
        monkeypatch.setattr("src.core.topology.discovery._forge", lambda: object())
        monkeypatch.setattr(
            "src.core.topology.discovery._token",
            lambda repository, forge=None: "an-app-token",
        )
        monkeypatch.setattr(
            "src.core.topology.discovery.head_revision",
            lambda repository, token: "abc123",
        )
        monkeypatch.setattr(
            "src.core.topology.discovery.already_read", lambda *args: False
        )
        monkeypatch.setattr(
            "src.api.routes.topology.provider_for", lambda configuration: Model()
        )
        yield
        app.dependency_overrides.pop(get_topology_repository, None)

    def entity(self, identifier, name):
        return self.client.put(
            "/api/topology/entities",
            json={
                "id": identifier,
                "kind": "repository",
                "status": "approved",
                "properties": {"name": name},
            },
            headers=self.headers,
        )

    def both(self):
        self.entity("checkout", "acme/checkout")
        self.entity("billing", "acme/billing")

    def discover(self, **overrides):
        return self.client.post(
            "/api/topology/discover",
            json={
                "assets": [
                    {"entity_id": "checkout", "repository": "acme/checkout"},
                    {"entity_id": "billing", "repository": "acme/billing"},
                ],
                "system_id": "system:commerce",
                **overrides,
            },
            headers=self.headers,
        )

    def test_asking_answers_before_anything_is_read(self):
        self.both()

        answered = self.discover()

        assert answered.status_code == 202
        asked = answered.json()["data"]
        assert asked["reading"] == ["acme/checkout", "acme/billing"]
        assert asked["total"] == 3

    def test_a_discovery_queues_a_manifest_pass_and_a_reading_for_each_repository(self):
        self.both()

        asked = self.discover().json()["data"]

        queued = job_store().in_batch(asked["batch_id"])
        assert [job.kind for job in queued] == [MANIFESTS, READING, READING]
        assert all(job.tenant == self.workspace_id for job in queued)
        assert all(job.payload["user"] == "1" for job in queued if job.kind == READING)

    def test_a_discovery_says_how_far_it_has_got(self):
        self.both()
        asked = self.discover().json()["data"]

        watched = self.client.get(
            f"/api/topology/discoveries/{asked['batch_id']}", headers=self.headers
        )

        assert watched.status_code == 200
        progress = watched.json()["data"]
        assert progress["total"] == 3
        assert progress["done"] == 0
        assert progress["finished"] is False
        assert [one["repository"] for one in progress["readings"]] == [
            "acme/checkout",
            "acme/billing",
        ]

    def test_a_repository_that_could_not_be_read_is_named_rather_than_counted(self):
        """The whole issue: a failure must never read as nothing found."""
        self.both()
        asked = self.discover().json()["data"]
        store = job_store()
        # Only this discovery's jobs are finished. The queue is shared, so
        # claiming is not the same as claiming what was just asked for.
        mine = {job.id for job in store.in_batch(asked["batch_id"])}
        while not store.read_batch(asked["batch_id"]).finished:
            claimed = store.claim("background", "worker-1", 10)
            if not claimed:
                break
            for lease in claimed:
                if lease.job.id in mine:
                    store.finish(lease, _outcome(lease.job.kind == READING))
                else:
                    store.release(lease)

        progress = self.client.get(
            f"/api/topology/discoveries/{asked['batch_id']}", headers=self.headers
        ).json()["data"]

        assert progress["finished"] is True
        assert progress["failed"] == 2
        # A reading is tried once, so a failed one is dead rather than failed:
        # anything reading this has to recognise both.
        refused = [one for one in progress["readings"] if one["state"] == DEAD]
        assert [one["repository"] for one in refused] == [
            "acme/checkout",
            "acme/billing",
        ]
        assert all(one["error"] for one in refused)

    def test_another_workspace_cannot_watch_this_discovery(self):
        self.both()
        asked = self.discover().json()["data"]
        elsewhere = {"Authorization": f"Bearer {_token(uuid.uuid4().hex)}"}

        watched = self.client.get(
            f"/api/topology/discoveries/{asked['batch_id']}", headers=elsewhere
        )

        assert watched.status_code == 404

    def test_reading_proposals_belong_to_the_system_that_requested_them(
        self, monkeypatch
    ):
        from src.core.topology.discovery import Readings
        from src.core.topology.models import TopologyEvidence, TopologyRelationship

        self.both()
        proposal = TopologyRelationship(
            id="checkout->billing:consumes",
            source_id="checkout",
            target_id="billing",
            type="consumes",
            status="pending",
            properties={"inferred_from": "reading"},
            evidence=(
                TopologyEvidence(id="reading", kind="reading", source="checkout"),
            ),
        )

        class Reading:
            refused = ""
            unfinished = False

            def __init__(self, *args):
                pass

            def propose(self, *args, **kwargs):
                return (proposal,)

        monkeypatch.setattr("src.core.topology.proposing.WhatItReads", Reading)
        configurations = []
        monkeypatch.setattr(
            "src.core.model.provider_for",
            lambda configuration: configurations.append(configuration) or Model(),
        )
        monkeypatch.setattr(
            "src.core.topology.store.topology_repository",
            lambda services: self.repository,
        )
        monkeypatch.setattr(
            "src.core.topology.reading.contents_reader", lambda *args: None
        )
        monkeypatch.setattr(
            "src.core.topology.discovery.remember_read", lambda *args: None
        )
        asked = self.discover(refresh=True).json()["data"]
        job = next(
            job
            for job in job_store().in_batch(asked["batch_id"])
            if job.kind == READING
        )

        assert Readings().run(job).succeeded
        assert configurations[0].user == "1"

        response = self.client.post(
            "/api/topology/search", json={}, headers=self.headers
        )
        assert response.status_code == 200
        saved = response.json()["data"]["relationships"][0]
        assert saved["properties"]["system_id"] == "system:commerce"
        assert saved["properties"]["inferred_from"] == "reading"
        assert saved["properties"]["provenance"]["evidence"][0]["id"] == "reading"
        assert saved["evidence"][0]["id"] == "reading"

    def test_a_repository_outside_the_workspace_is_refused(self):
        self.both()

        answered = self.discover(
            assets=[{"entity_id": "checkout", "repository": "acme/elsewhere"}]
        )

        assert answered.status_code == 403

    def test_an_asset_nobody_recorded_is_refused(self):
        self.both()

        answered = self.discover(
            assets=[{"entity_id": "nobody", "repository": "acme/checkout"}]
        )

        assert answered.status_code == 422

    def test_nothing_is_queued_where_there_is_no_model_to_read_with(self, monkeypatch):
        """Found once, before anything is queued, rather than by every job."""
        self.both()
        monkeypatch.setattr(
            "src.api.routes.topology.provider_for", lambda configuration: None
        )

        answered = self.discover()

        assert answered.status_code == 400


def _outcome(failed: bool):
    from src.core.jobs.models import JobOutcome

    return (
        JobOutcome.failed("the model could not be asked") if failed else JobOutcome.ok()
    )
