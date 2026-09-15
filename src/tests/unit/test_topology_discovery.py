"""A discovery is queued, kept, and never reports a failure as nothing found."""

from __future__ import annotations

import pytest

from src.core.jobs.memory import InMemoryJobStore
from src.core.jobs.models import BACKGROUND, BY_WORKSPACE, Job, JobOutcome
from src.core.services import ServiceRegistry
from src.core.topology import discovery
from src.core.topology.discovery import (
    MANIFESTS,
    MOST_READ,
    READING,
    Readings,
    discover,
    manifests_job,
    reading_job,
)
from src.core.jobs.interfaces import JobQueue, JobStore

ASSETS = [
    {"entity_id": "asset:checkout", "repository": "acme/checkout"},
    {"entity_id": "asset:billing", "repository": "acme/billing"},
]


@pytest.fixture
def queue():
    """A store nothing else shares, registered as both queue and store."""
    store = InMemoryJobStore()
    services = ServiceRegistry()
    services.register(JobStore, store, "test")
    services.register(JobQueue, store, "test")
    return store, services


@pytest.fixture(autouse=True)
def _no_forge(monkeypatch):
    """Nothing in a unit test reaches GitHub."""
    monkeypatch.setattr(discovery, "_forge", lambda: object())
    monkeypatch.setattr(discovery, "_token", lambda repository, forge=None: "a-token")
    monkeypatch.setattr(discovery, "head_revision", lambda repository, token: "abc123")
    monkeypatch.setattr(discovery, "already_read", lambda *args: False)
    monkeypatch.setattr(discovery, "remember_read", lambda *args: None)


def _asked(services, **overrides):
    asked = {
        "workspace": "workspace-1",
        "system_id": "system:commerce",
        "targets": [one["entity_id"] for one in ASSETS],
        "about": {"asset:checkout": "acme/checkout", "asset:billing": "acme/billing"},
        "services": services,
    }
    asked.update(overrides)
    return discover(ASSETS, **asked)


def test_a_discovery_is_one_batch_of_a_manifest_pass_and_a_reading_each(queue):
    store, services = queue

    asked = _asked(services)

    jobs = store.in_batch(asked["batch_id"])
    assert [job.kind for job in jobs] == [MANIFESTS, READING, READING]
    assert asked["total"] == 3
    assert asked["reading"] == ["acme/checkout", "acme/billing"]


def test_every_job_of_a_discovery_is_charged_to_the_workspace_that_asked(queue):
    store, services = queue

    asked = _asked(services)

    for job in store.in_batch(asked["batch_id"]):
        assert job.tenant == "workspace-1"
        assert job.tenant_kind == BY_WORKSPACE
        assert job.lane == BACKGROUND


def test_a_reading_is_never_offered_the_repository_it_is_reading(queue):
    store, services = queue

    asked = _asked(services)

    readings = [job for job in store.in_batch(asked["batch_id"]) if job.kind == READING]
    for job in readings:
        assert job.payload["entity_id"] not in job.payload["targets"]


def test_a_repository_that_has_not_moved_is_not_read_again(queue, monkeypatch):
    store, services = queue
    monkeypatch.setattr(
        discovery,
        "already_read",
        lambda repository, revision, targets: repository == "acme/billing",
    )

    asked = _asked(services)

    assert asked["reading"] == ["acme/checkout"]
    assert asked["reused"] == ["acme/billing"]
    # The manifest pass still sees every repository: a name is only a
    # dependency once another repository is found publishing it.
    manifests = [
        job for job in store.in_batch(asked["batch_id"]) if job.kind == MANIFESTS
    ]
    assert len(manifests[0].payload["assets"]) == 2


def test_asking_once_cannot_read_more_than_a_discovery_pays_for(queue, monkeypatch):
    store, services = queue
    monkeypatch.setattr(discovery, "MOST_READ", 1)
    many = [{"entity_id": f"asset:{n}", "repository": f"acme/{n}"} for n in range(4)]

    asked = discover(
        many,
        workspace="workspace-1",
        system_id="system:commerce",
        targets=[one["entity_id"] for one in many],
        about={},
        services=services,
    )

    assert len(asked["reading"]) == 1
    assert asked["not_read"] == 3


def test_a_reading_is_tried_once_because_a_second_pays_for_the_whole_thing_again():
    job = reading_job(
        ASSETS[0],
        workspace="workspace-1",
        system_id="system:commerce",
        targets=["asset:billing"],
        about="- asset:billing: acme/billing",
    )

    assert job.max_attempts == 1
    assert job.deadline_seconds == discovery.READING_TIMEOUT


def test_a_job_with_no_workspace_writes_nowhere_rather_than_raising():
    job = Job(
        id=1,
        lane=BACKGROUND,
        kind=READING,
        payload={"entity_id": "a", "repository": "acme/a", "targets": ["b"]},
        state="running",
    )

    outcome = Readings().run(job)

    assert not outcome.succeeded
    assert "workspace" in outcome.error


def test_a_reading_the_model_refused_is_a_failure_not_an_empty_answer(monkeypatch):
    """The whole point of the issue: a reading that never ran is not an answer."""

    class Refused:
        refused = "the model could not be asked: TimeoutError"

        def __init__(self, *args, **kwargs):
            pass

        def propose(self, *args, **kwargs):
            return ()

    monkeypatch.setattr(discovery, "_token", lambda repository, forge=None: "a-token")
    monkeypatch.setattr("src.core.topology.proposing.WhatItReads", Refused)
    monkeypatch.setattr(
        "src.core.model.provider_for", lambda configuration, **kwargs: object()
    )
    monkeypatch.setattr(
        "src.core.topology.store.topology_repository", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "src.core.topology.reading.contents_reader",
        lambda *args, **kwargs: (lambda path: None),
    )

    job = Job(
        id=1,
        lane=BACKGROUND,
        kind=READING,
        payload={
            "entity_id": "asset:checkout",
            "repository": "acme/checkout",
            "targets": ["asset:billing"],
            "workspace": "workspace-1",
            "persist": False,
        },
        state="running",
    )

    outcome = Readings().run(job)

    assert not outcome.succeeded
    assert "could not be asked" in outcome.error


def test_a_manifest_pass_carries_every_asset_it_was_given():
    job = manifests_job(ASSETS, workspace="workspace-1", system_id="system:commerce")

    assert job.kind == MANIFESTS
    assert [one["repository"] for one in job.payload["assets"]] == [
        "acme/checkout",
        "acme/billing",
    ]


def test_most_read_is_a_ceiling_on_what_one_asking_spends():
    assert MOST_READ >= 1


def test_where_every_repository_stands_is_asked_at_once(queue, monkeypatch):
    """Serially, this is what the discovery was queued to avoid.

    Queueing asks a forge about every repository before it answers, so a
    caller waits for all of it however little each one costs.
    """
    import threading

    running = []
    highest = []

    def _slow(repository, token):
        running.append(repository)
        highest.append(len(running))
        # Long enough that a serial run could not overlap two of them.
        threading.Event().wait(0.05)
        running.pop()
        return "abc123"

    store, services = queue
    monkeypatch.setattr(discovery, "head_revision", _slow)
    many = [{"entity_id": f"asset:{n}", "repository": f"acme/{n}"} for n in range(6)]

    discover(
        many,
        workspace="workspace-1",
        system_id="system:commerce",
        targets=[one["entity_id"] for one in many],
        about={},
        services=services,
    )

    assert max(highest) > 1


def test_a_repository_held_twice_is_asked_about_once(queue, monkeypatch):
    """A monorepo is several parts of one system and one repository."""
    asked = []
    store, services = queue
    monkeypatch.setattr(
        discovery,
        "head_revision",
        lambda repository, token: asked.append(repository) or "abc123",
    )
    monorepo = [
        {"entity_id": "asset:api", "repository": "acme/platform"},
        {"entity_id": "asset:web", "repository": "acme/platform"},
        {"entity_id": "asset:jobs", "repository": "acme/platform"},
    ]

    discover(
        monorepo,
        workspace="workspace-1",
        system_id="system:commerce",
        targets=[one["entity_id"] for one in monorepo],
        about={},
        services=services,
    )

    assert asked == ["acme/platform"]


def test_a_repository_nobody_can_place_does_not_sink_the_discovery(queue, monkeypatch):
    """Whatever went wrong with one, the rest are still worth queueing."""
    store, services = queue

    def _breaks(repository, token):
        if repository == "acme/billing":
            raise RuntimeError("the forge said something unexpected")
        return "abc123"

    monkeypatch.setattr(discovery, "head_revision", _breaks)

    asked = _asked(services)

    assert asked["reading"] == ["acme/checkout", "acme/billing"]
    assert len(store.in_batch(asked["batch_id"])) == 3


def test_one_forge_answers_for_every_repository_in_a_discovery(queue, monkeypatch):
    """A client per repository pays the two requests that mint a token again."""
    built = []

    def _counted():
        built.append(1)
        return object()

    store, services = queue
    monkeypatch.setattr(discovery, "_forge", _counted)
    monkeypatch.setattr(discovery, "_token", lambda repository, forge=None: "a-token")

    _asked(services)

    assert len(built) == 1
