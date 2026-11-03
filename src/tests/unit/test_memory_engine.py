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
        assert request.headers["Memory-Actor"] == "test-person-token"
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
            assert json.loads(request.content)["filters"] == {"buckets": [event["bucket"]]}
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
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
        if request.url.path.startswith("/v1/entities/"):
            return httpx.Response(404)
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
        assert recalled.actors[0].type == entity["type"]
        assert recalled.source["url"] == event["source"]["url"]
        assert recalled.source["container_url"] == ""
        memory.use([event["id"]])
    finally:
        memory.close()
    assert len(calls) == 12


def test_engine_preserves_service_and_group_actors_and_delegation():
    event = example("event.review-comment.json")
    actor = example("entity.actor.json")
    service = {**actor, "id": "ac_service", "type": "service"}
    group = {**actor, "id": "ac_group", "type": "group"}
    event["participants"] = [
        {"actor": service["id"], "role": "author"},
        {"actor": group["id"], "role": "on_behalf_of"},
    ]
    entities = {entity["id"]: entity for entity in (service, group)}

    def handle(request):
        if request.method == "PUT":
            return httpx.Response(200, json={"id": event["bucket"]})
        if request.url.path == "/v1/recall":
            assert json.loads(request.content)["filters"] == {"buckets": [event["bucket"]]}
            return httpx.Response(200, json={"results": [{
                "id": event["id"], "record": event, "score": 0.75,
            }]})
        entity = entities.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=entity) if entity else httpx.Response(404)

    memory = TrailpadMemory(
        "https://memory.example", "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        recalled = memory.recall(Scope.from_mapping({}), "retry").results[0]
        assert [(actor.type, actor.role) for actor in recalled.actors] == [
            ("service", "author"), ("group", "on_behalf_of"),
        ]
    finally:
        memory.close()


def test_engine_caches_bucket_binding_and_forwards_listing_filters():
    event = example("event.review-comment.json")
    entity = example("entity.actor.json")
    bindings = []

    def handle(request):
        if request.method == "PUT":
            bindings.append(json.loads(request.content))
            return httpx.Response(200, json={"id": event["bucket"]})
        if request.url.path.startswith(("/v1/entities/", "/v1/events/")):
            return httpx.Response(404)
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


@pytest.mark.parametrize("legacy_bot", [False, True])
def test_engine_maps_captured_delivery_page_and_fetches_parent(legacy_bot):
    from dataclasses import asdict
    from src.core.memory.github import Bound, translate

    rows = json.loads(
        (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
    )
    row = next(row for row in rows if row["type"] == "pull_request_review_comment" and row["payload"]["comment"]["user"]["type"] == "Bot")
    batch = translate(
        Bound("captured", "installation", "922971929"),
        row["type"], row["action"], row["payload"],
    )
    events = {event["id"]: event for event in batch["events"]}
    entities = {entity["id"]: entity for entity in batch["entities"]}
    comment = next(
        event for event in events.values()
        if event["type"] == "github.pull_request.review_comment"
    )
    parent = events[comment["part_of"]]
    if legacy_bot:
        for entity in entities.values():
            if entity["type"] == "bot":
                entity["type"] = "software.bot"
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "captured"})
        if request.url.path == "/v1/events":
            return httpx.Response(200, json={
                "events": [comment], "entities": list(entities.values()),
                "next": comment["id"],
            })
        if request.url.path == "/v1/events/" + parent["id"]:
            return httpx.Response(200, json=parent)
        if request.url.path == "/v1/recall":
            assert json.loads(request.content)["filters"] == {"buckets": ["captured"]}
            return httpx.Response(200, json={"results": [{
                "id": comment["id"], "record": comment,
                "score": 0.75,
            }]})
        if request.url.path.startswith("/v1/entities/"):
            return httpx.Response(200, json=entities[request.url.path.rsplit("/", 1)[1]])
        raise AssertionError(request.url)

    memory = TrailpadMemory(
        "https://memory.example", "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        scope = Scope.from_mapping({})
        page = memory.history(scope)
        view, context = page.events
        assert page.next == comment["id"]
        assert page.configured
        assert view.part_of == context.id == parent["id"]
        assert not view.outside_window
        assert context.outside_window
        user = row["payload"]["comment"]["user"]
        assert view.actors[0].name == user["login"]
        assert view.actors[0].image == user["avatar_url"]
        assert view.actors[0].url == user["html_url"]
        repository = row["payload"]["repository"]
        assert view.source["container"] == repository["full_name"]
        assert view.source["container_url"] == repository["html_url"]
        assert view.source["url"] == row["payload"]["comment"]["html_url"]
        assert view.title == context.title == row["payload"]["pull_request"]["title"]
        assert view.actors[0].type == ("bot" if user["type"] == "Bot" else "person")
        assert any(s.name == row["payload"]["comment"]["path"] for s in view.subjects)
        recalled = memory.recall(scope, view.what).results[0]
        assert recalled.actors == view.actors
        assert recalled.subjects == view.subjects
        assert recalled.score == 0.75
        serialized = asdict(view)
        assert "bucket" not in serialized
        assert "appraisals" not in serialized
        assert calls.count("/v1/events/" + parent["id"]) == 1
    finally:
        memory.close()
