import json
from pathlib import Path

import httpx
import pytest

from src.core.memory import TrailpadMemory, Entity, Event, InMemoryMemory
from src.core.scope import Scope

FIXTURES = Path(__file__).parents[1] / "fixtures" / "memory"


def example(name):
    return json.loads((FIXTURES / name).read_text())


def test_engine_uses_exact_record_shapes_and_bearer_headers():
    event = example("event.review-comment.json")
    entity = example("entity.actor.json")
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-service-key"
        assert request.headers["Memory-Person"] == "test-person-token"
        if request.url.path == "/v1/records":
            assert json.loads(request.content) == {
                "events": [event],
                "entities": [entity],
            }
            return httpx.Response(
                200,
                json={"stored": {event["id"]: event["id"], entity["id"]: entity["id"]}},
            )
        if request.url.path == "/v1/events/" + event["id"]:
            return httpx.Response(200, json=event)
        if request.url.path == "/v1/entities/" + entity["id"]:
            return httpx.Response(200, json=entity)
        if request.method == "PUT":
            return httpx.Response(200, json={"id": event["bucket"]})
        if request.url.path == "/v1/recall":
            assert json.loads(request.content)["filters"]["kinds"] == ["event"]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "kind": "event",
                            "id": event["id"],
                            "record": event,
                            "score": 1.0,
                        }
                    ]
                },
            )
        if request.url.path == "/v1/events/use":
            assert json.loads(request.content) == {"events": [event["id"]]}
            return httpx.Response(204)
        raise AssertionError(request.url)

    memory = TrailpadMemory(
        "https://memory.example",
        "test-service-key",
        person="test-person-token",
        transport=httpx.MockTransport(handle),
    )
    try:
        memory.record([Event.from_dict(event)], [Entity.from_dict(entity)])
        assert memory.event(event["id"]).to_dict() == event
        assert memory.entity(entity["id"]).to_dict() == entity
        recalled = memory.recall(Scope.from_mapping({}), "retry").results[0]
        assert recalled.what == event["content"][0]["text"]
        assert recalled.why == event["why"]
        assert recalled.who == entity["name"]
        assert recalled.score == 1.0
        memory.use([event["id"]])
    finally:
        memory.close()
    assert len(calls) == 7


def test_engine_caches_bucket_binding_and_forwards_listing_filters():
    event = example("event.review-comment.json")
    entity = example("entity.actor.json")
    bindings = []

    def handle(request):
        if request.method == "PUT":
            bindings.append(json.loads(request.content))
            return httpx.Response(200, json={"id": event["bucket"]})
        assert request.url.params["bucket"] == event["bucket"]
        assert request.url.params["from"] == "2026-04-01T00:00:00Z"
        assert request.url.params["to"] == "2026-05-01T00:00:00Z"
        assert request.url.params["after"] == "previous"
        return httpx.Response(
            200, json={"events": [event], "entities": [entity], "next": event["id"]}
        )

    memory = TrailpadMemory(
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        scope = Scope.from_mapping({"repository": "acme/shop"})
        memory.bucket(scope)
        page = memory.history(
            scope, "2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z", "previous"
        )
        assert page.events[0].what == event["content"][0]["text"]
        assert page.events[0].who == entity["name"]
        assert page.next == event["id"]
        assert len(bindings) == 1
        assert bindings[0]["binding"]["system"] == "sourceant"
    finally:
        memory.close()


def test_engine_propagates_conflicts():
    memory = TrailpadMemory(
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                409, json={"error": "already recorded with different content"}
            )
        ),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            memory.record([Event.from_dict(example("event.review-comment.json"))], [])
    finally:
        memory.close()


def test_in_memory_records_are_idempotent_and_defensively_copied():
    memory = InMemoryMemory()
    event = Event.from_dict(example("event.review-comment.json"))
    memory.record([event], [])
    memory.record([event], [])
    assert len(memory.events) == 1
    event.content[0]["text"] = "Changed"
    with pytest.raises(ValueError):
        memory.record([event], [])
    assert memory.events[event.id]["content"][0]["text"] != "Changed"


def test_in_memory_listing_filters_scope_time_and_cursor():
    from dataclasses import replace

    memory = InMemoryMemory()
    scope = Scope.from_mapping({"repository": "acme/shop"})
    other = Scope.from_mapping({"repository": "acme/other"})
    original = Event.from_dict(example("event.review-comment.json"))
    first = replace(original, bucket=memory.bucket(scope), id="first")
    second = replace(first, id="second", occurred_at="2026-04-20T10:00:00+02:00")
    foreign = replace(first, bucket=memory.bucket(other), id="foreign")
    memory.record([first, second, foreign], [])
    page = memory.list(scope, "2026-04-19T00:00:00Z", "2026-04-21T00:00:00Z", "first")
    assert [event["id"] for event in page["events"]] == ["second"]
    assert page["entities"] == []
    assert [
        event["id"]
        for event in memory.list(scope, until="2026-04-20T08:00:00Z")["events"]
    ] == ["first"]
    page["events"][0]["content"][0]["text"] = "Changed"
    assert memory.events["second"]["content"][0]["text"] != "Changed"


def test_remember_constructs_engine_records_from_scope_observation():
    from datetime import datetime
    from src.core.memory import Remembered

    event = example("event.review-comment.json")
    actor = example("entity.actor.json")
    records = []

    def handle(request):
        if request.method == "PUT":
            return httpx.Response(200, json={"id": event["bucket"]})
        assert request.url.path == "/v1/records"
        records.append(json.loads(request.content))
        return httpx.Response(204)

    memory = TrailpadMemory(
        "https://memory.example", "test-service-key", transport=httpx.MockTransport(handle)
    )
    try:
        observation = Remembered(
            event["content"][0]["text"],
            datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")),
            why=event["why"],
        )
        result = memory.remember(Scope.from_mapping({"actor": actor["name"]}), observation)
        assert result.what == observation.what
        assert result.who == actor["name"]
        assert result.when == observation.when
        stored = records[0]["events"][0]
        assert stored["id"] == result.id
        assert stored["type"] == observation.type
        assert stored["content"][0]["text"] == observation.what
        assert records[0]["entities"][0]["name"] == result.who
        assert stored["participants"][0]["actor"] == records[0]["entities"][0]["id"]
    finally:
        memory.close()
