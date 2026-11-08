import json
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.core.memory import (
    Actor,
    Event,
    InMemoryMemory,
    Source,
    Subject,
    TrailpadMemory,
)
from src.core.memory.engine import Entity, EventRecord
from src.core.scope import Scope


@pytest.mark.parametrize("adapter", ["in_process", "http"])
@pytest.mark.parametrize(
    "offset", [timezone.utc, timezone(timedelta(hours=5, minutes=30))]
)
def test_read_views_roundtrip_stored_timestamp_bytes(monkeypatch, adapter, offset):
    monkeypatch.setattr("src.config.db.get_engine", lambda: None)
    backend = InMemoryMemory()
    writes = []

    def handle(request):
        if request.method == "PUT":
            binding = json.loads(request.content)["binding"]
            return httpx.Response(
                200,
                json={
                    "id": backend.bind(
                        binding["system"], binding["installation"], binding["container"]
                    )
                },
            )
        if request.url.path == "/v1/records":
            body = json.loads(request.content)
            writes.append(body)
            backend._write_records(
                [EventRecord.from_dict(event) for event in body["events"]],
                [Entity.from_dict(entity) for entity in body["entities"]],
            )
            return httpx.Response(204)
        if request.url.path == "/v1/events":
            buckets = request.url.params.get_list("bucket")
            return httpx.Response(
                200,
                json={
                    "events": [
                        event
                        for event in backend._events.values()
                        if event["bucket"] in buckets
                    ],
                    "entities": list(backend.entities.values()),
                    "next": None,
                },
            )
        raise AssertionError(request.url)

    memory = (
        backend
        if adapter == "in_process"
        else TrailpadMemory(
            "https://memory.example",
            "test-service-key",
            transport=httpx.MockTransport(handle),
        )
    )
    scope = Scope.from_mapping(
        {"workspace": "team", "repository": "acme/shop", "branch": "main"}
    )
    source = Source.for_scope(scope)
    event = Event(
        id="experiment",
        type="sourceant.observation",
        source=source,
        when=datetime(2026, 4, 19, 14, 2, 11, 120000, tzinfo=offset),
        ended_at=datetime(2026, 4, 19, 15, 2, 11, 123456, tzinfo=offset),
        what="Retry succeeded",
        actors=[Actor(id="ana", name="Ana", role="author")],
        subjects=[Subject(id="path:retry.py", type="code.path", name="retry.py")],
        valence=1,
        importance=3,
    )
    try:
        (stored,) = memory.record(scope, [event])
        before = deepcopy(backend._events[stored.id])
        (view,) = memory.events(scope).events
        assert isinstance(view.when, datetime)
        assert view.when == event.when
        assert set(asdict(view.source)) == {
            "system",
            "installation",
            "container",
            "url",
            "container_url",
            "native_id",
            "id_namespace",
        }
        (repeated,) = memory.record(
            scope, [replace(view, score=0.9, outside_window=True)]
        )
        assert repeated.id == stored.id
        assert backend._events[stored.id] == before
        assert backend._events[stored.id]["occurred_at"] == before["occurred_at"]
        assert backend._events[stored.id]["ended_at"] == before["ended_at"]
        assert len(backend._events) == 1
        if adapter == "http":
            assert writes[1] == writes[0]
    finally:
        if adapter == "http":
            memory.close()


@pytest.mark.parametrize("subject_id", ["sb_customer", "pl_customer"])
def test_source_subject_ids_are_mapped_without_prefix_ownership(subject_id):
    memory = InMemoryMemory()
    for system in ("crm", "billing"):
        scope = Scope.from_mapping({"repository": system})
        event = Event(
            id="created",
            type="customer.created",
            source=Source(system, "account", "customers"),
            subjects=[Subject(id=subject_id, type="customer", name="Customer")],
        )
        memory.record(scope, [event])
    entities = [
        entity for entity in memory.entities.values() if entity["type"] == "customer"
    ]
    assert len(entities) == 2
    assert len({entity["id"] for entity in entities}) == 2
    assert all(entity["id"] != subject_id for entity in entities)


def test_source_actor_ids_are_mapped_even_when_they_look_mapped():
    memory = InMemoryMemory()
    event = Event(
        id="created",
        type="customer.created",
        source=Source("crm", "account", "customers", id_namespace="customers"),
        actors=[
            Actor(id="customer", name="First"),
            Actor(id="ac_customers_customer", name="Second"),
        ],
    )
    memory.record(Scope(), [event])
    actors = [
        entity for entity in memory.entities.values() if entity["class"] == "actor"
    ]
    assert len(actors) == 2
    assert len({actor["id"] for actor in actors}) == 2
    assert all(
        actor["id"] not in {"customer", "ac_customers_customer"} for actor in actors
    )


def test_engine_links_are_read_from_the_captured_engine_response():
    from pathlib import Path

    captured = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "memory"
            / "engine-event-links.json"
        ).read_text()
    )
    pages = captured["pages"]
    bucket = pages[0]["request"]["buckets"][0]
    asked = []

    def handle(request):
        if request.method == "PUT":
            return httpx.Response(200, json={"id": bucket})
        if request.url.path == "/v1/events/links":
            asked.append(json.loads(request.content))
            return httpx.Response(200, json=pages[len(asked) - 1]["response"])
        raise AssertionError(request.url)

    memory = TrailpadMemory(
        "http://memory.test", "key", transport=httpx.MockTransport(handle)
    )
    scope = Scope.from_mapping(
        {"provider": "github", "repository": "sourceant/sourceant"}
    )
    links = memory.links(
        scope,
        since="2000-01-01T00:00:00Z",
        until="2100-01-01T00:00:00Z",
        page_size=pages[0]["request"]["anchors_limit"],
    )

    assert asked == [page["request"] for page in pages]
    pairs = {(link.source, link.target): link for link in links}
    every = [link for page in pages for link in page["response"]["links"]]
    assert set(pairs) == {(link["from"], link["to"]) for link in every}
    for link in every:
        assert pairs[(link["from"], link["to"])].score >= link["score"]
    assert [link.score for link in links] == sorted(
        (link.score for link in links), reverse=True
    )
    stored = pairs[tuple(sorted((captured["decision"], captured["push"])))]
    assert stored.score == 1
    assert stored.because["explicit"]["links"][0]["type"] == "derived_from"
    about_file = [
        link
        for link in links
        if captured["decision"] in (link.source, link.target)
        and "subject" in link.because
    ]
    assert about_file
    assert all(
        link.because["subject"]["names"] == captured["touched"] for link in about_file
    )
    assert any("meaning" in link.because for link in links)
