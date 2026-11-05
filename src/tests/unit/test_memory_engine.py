import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from src.core.memory import (
    TrailpadMemory,
    InMemoryMemory,
    Event,
    Actor,
    Subject,
    Source,
)
from src.core.memory.engine import Entity, EventRecord
from src.core.scope import Scope

FIXTURES = Path(__file__).parents[1] / "fixtures" / "memory"


@pytest.fixture(autouse=True)
def no_application_database(monkeypatch):
    monkeypatch.setattr("src.config.db.get_engine", lambda: None)


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
            assert json.loads(request.content)["filters"] == {
                "buckets": [event["bucket"]]
            }
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
        memory._write_records(
            [EventRecord.from_dict(event)], [Entity.from_dict(entity)]
        )
        assert memory.event(event["id"]).to_dict() == event
        assert memory.entity(entity["id"]).to_dict() == entity
        recalled = memory.recall(Scope.from_mapping({}), "retry")[0]
        assert recalled.what == event["content"][0]["text"]
        assert recalled.why == event["why"]
        assert ", ".join(actor.name for actor in recalled.actors) == entity["name"]
        assert recalled.score == 1.0
        assert recalled.actors[0].type == entity["type"]
        assert recalled.source.url == event["source"]["url"]
        assert recalled.source.container_url == ""
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
            assert json.loads(request.content)["filters"] == {
                "buckets": [event["bucket"]]
            }
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": event["id"],
                            "record": event,
                            "score": 0.75,
                        }
                    ]
                },
            )
        entity = entities.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json=entity) if entity else httpx.Response(404)

    memory = TrailpadMemory(
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        recalled = memory.recall(Scope.from_mapping({}), "retry")[0]
        assert [(actor.type, actor.role) for actor in recalled.actors] == [
            ("service", "author"),
            ("group", "on_behalf_of"),
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
        page = memory.events(
            scope, "2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z", "previous"
        )
        assert page.events[0].what == event["content"][0]["text"]
        assert (
            ", ".join(actor.name for actor in page.events[0].actors) == entity["name"]
        )
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
            memory._write_records(
                [EventRecord.from_dict(example("event.review-comment.json"))], []
            )
    finally:
        memory.close()


def test_in_memory_records_are_idempotent_and_defensively_copied():
    memory = InMemoryMemory()
    event = EventRecord.from_dict(example("event.review-comment.json"))
    memory._write_records([event], [])
    memory._write_records([event], [])
    assert len(memory._events) == 1
    event.content[0]["text"] = "Changed"
    with pytest.raises(ValueError):
        memory._write_records([event], [])
    assert memory._events[event.id]["content"][0]["text"] != "Changed"


def test_in_memory_listing_filters_scope_time_and_cursor():
    from dataclasses import replace

    memory = InMemoryMemory()
    scope = Scope.from_mapping({"repository": "acme/shop"})
    other = Scope.from_mapping({"repository": "acme/other"})
    original = EventRecord.from_dict(example("event.review-comment.json"))
    first = replace(original, bucket=memory.bucket(scope), id="first")
    second = replace(
        first, id="second", occurred_at="2026-04-20T10:00:00+02:00", source=None
    )
    foreign = replace(first, bucket=memory.bucket(other), id="foreign")
    memory._write_records([first, second, foreign], [])
    page = memory.list(scope, "2026-04-19T00:00:00Z", "2026-04-21T00:00:00Z", "first")
    assert [event["id"] for event in page["events"]] == ["second"]
    assert page["entities"] == []
    assert [
        event["id"]
        for event in memory.list(scope, until="2026-04-20T08:00:00Z")["events"]
    ] == ["first"]
    page["events"][0]["content"][0]["text"] = "Changed"
    assert memory._events["second"]["content"][0]["text"] != "Changed"


def test_record_constructs_engine_records_and_returns_stored_views():
    from datetime import datetime

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
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        observation = Event(
            id="experiment",
            type="sourceant.observation",
            source=Source("sourceant", "local", "shop"),
            what=event["content"][0]["text"],
            when=datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")),
            why=event["why"],
            actors=[Actor(id=actor["name"], name=actor["name"], role="author")],
        )
        (result,) = memory.record(
            Scope.from_mapping({"actor": actor["name"]}), [observation]
        )
        assert result.what == observation.what
        assert ", ".join(actor.name for actor in result.actors) == actor["name"]
        assert result.when == observation.when
        stored = records[0]["events"][0]
        assert stored["id"] == result.id
        assert stored["type"] == observation.type
        assert stored["content"][0]["text"] == observation.what
        assert records[0]["entities"][0]["name"] == ", ".join(
            actor.name for actor in result.actors
        )
        assert stored["participants"][0]["actor"] == records[0]["entities"][0]["id"]
    finally:
        memory.close()


@pytest.mark.parametrize("legacy_bot", [False, True])
def test_engine_maps_captured_delivery_page_and_fetches_parent(legacy_bot):
    from dataclasses import asdict
    from src.integrations.github.memory_source import Origin, translate

    rows = json.loads(
        (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
    )
    row = next(
        row
        for row in rows
        if row["type"] == "pull_request_review_comment"
        and row["payload"]["comment"]["user"]["type"] == "Bot"
    )
    occurrences = translate(
        Origin("installation", "922971929"),
        row["type"],
        row["action"],
        row["payload"],
    )
    mapped = InMemoryMemory()
    mapped.bind = lambda *args: "captured"
    mapped.record(Scope.from_mapping({}), occurrences)
    events = mapped._events
    entities = mapped.entities
    comment = next(
        event
        for event in events.values()
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
            return httpx.Response(
                200,
                json={
                    "events": [comment],
                    "entities": list(entities.values()),
                    "next": comment["id"],
                },
            )
        if request.url.path == "/v1/events/" + parent["id"]:
            return httpx.Response(200, json=parent)
        if request.url.path == "/v1/recall":
            assert json.loads(request.content)["filters"] == {"buckets": ["captured"]}
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": comment["id"],
                            "record": comment,
                            "score": 0.75,
                        }
                    ]
                },
            )
        if request.url.path.startswith("/v1/entities/"):
            return httpx.Response(
                200, json=entities[request.url.path.rsplit("/", 1)[1]]
            )
        raise AssertionError(request.url)

    memory = TrailpadMemory(
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        scope = Scope.from_mapping({})
        page = memory.events(scope)
        view = next(event for event in page.events if event.id == comment["id"])
        context = next(event for event in page.events if event.id == parent["id"])
        assert page.next == comment["id"]
        assert memory.configured
        assert view.part_of == context.id == parent["id"]
        assert not view.outside_window
        assert context.outside_window
        user = row["payload"]["comment"]["user"]
        assert view.actors[0].name == user["login"]
        assert view.actors[0].image == user["avatar_url"]
        assert view.actors[0].url == user["html_url"]
        repository = row["payload"]["repository"]
        assert view.source.container == str(repository["id"])
        assert view.source.container_url == repository["html_url"]
        assert view.source.url == row["payload"]["comment"]["html_url"]
        assert view.title == context.title == row["payload"]["pull_request"]["title"]
        assert view.actors[0].type == ("bot" if user["type"] == "Bot" else "person")
        assert any(s.name == row["payload"]["comment"]["path"] for s in view.subjects)
        recalled = memory.recall(scope, view.what)[0]
        assert recalled.actors == view.actors
        assert recalled.subjects == view.subjects
        assert recalled.score == 0.75
        serialized = asdict(view)
        assert "bucket" not in serialized
        assert "appraisals" not in serialized
        assert calls.count("/v1/events/" + parent["id"]) == 1
    finally:
        memory.close()


def test_captured_github_translation_preserves_original_records():
    from src.integrations.github.memory_source import Origin, translate

    rows = json.loads(
        (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
    )
    expected = example("captured-github-records.json")
    heads = {}
    for row, original in zip(rows, expected, strict=True):
        payload = row["payload"]
        pull = payload.get("pull_request")
        if pull and (pull.get("head") or {}).get("ref"):
            heads[pull["head"]["ref"]] = pull
        occurrences = translate(
            Origin("installation", "922971929"),
            row["type"],
            row.get("action"),
            payload,
            heads.get,
        )
        if original is None:
            assert occurrences is None
            continue
        memory = InMemoryMemory()
        scopes = [
            Scope(),
            Scope.from_mapping({"workspace": "first"}),
            Scope.from_mapping({"workspace": "second"}),
        ]
        ids = []
        for scope in scopes:
            views = memory.record(scope, occurrences)
            before = deepcopy(memory._events)
            assert [view.id for view in memory.record(scope, occurrences)] == [
                view.id for view in views
            ]
            assert memory._events == before
            ids.append({view.id for view in views})
            assert len(views) == len(original["events"])
            for occurrence, view in zip(occurrences, views, strict=True):
                assert view.source.system == occurrence.source.system
                assert view.source.installation == occurrence.source.installation
                assert view.source.container == "922971929"
                assert view.source.native_id == occurrence.source.native_id
                assert view.source.url == occurrence.source.url
                assert view.what == occurrence.what
                assert "sourceant.source" not in memory._events[view.id].get(
                    "attributes", {}
                )
            stored = deepcopy(memory._events)
            entities = deepcopy(memory.entities)
            memory.record(scope, memory.events(scope).events)
            assert memory._events == stored
            assert memory.entities == entities
        assert all(
            left.isdisjoint(right)
            for i, left in enumerate(ids)
            for right in ids[i + 1 :]
        )


def test_history_resolves_buckets_once_for_several_external_parents(monkeypatch):
    from dataclasses import replace

    memory = InMemoryMemory()
    scope = Scope.from_mapping({})
    bucket = memory.bucket(scope)
    original = EventRecord.from_dict(example("event.review-comment.json"))
    parents = [
        replace(
            original,
            bucket=bucket,
            id=f"parent-{i}",
            occurred_at="2020-01-01T00:00:00Z",
            part_of="",
            source=None,
        )
        for i in range(3)
    ]
    children = [
        replace(
            original, bucket=bucket, id=f"child-{i}", part_of=parent.id, source=None
        )
        for i, parent in enumerate(parents)
    ]
    memory._write_records(parents + children, [])
    calls = []
    monkeypatch.setattr(memory, "bucket", lambda scope: calls.append(scope) or bucket)
    page = memory.events(scope, since="2026-01-01T00:00:00Z")
    assert sum(event.outside_window for event in page.events) == 3
    assert calls == [scope]


def test_github_source_does_not_import_adapter():
    import ast
    from importlib.util import find_spec

    origin = Path(find_spec("src.integrations.github.memory_source").origin)
    if origin.suffix != ".py":
        pytest.skip("compiled builds do not ship module source")
    tree = ast.parse(origin.read_text())
    imports = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert [
        module for module in imports if module and module.startswith("src.core.memory")
    ] == ["src.core.memory.models"]
    assert not any(
        isinstance(node, ast.Import)
        and any(alias.name.startswith("src.core.memory") for alias in node.names)
        for node in ast.walk(tree)
    )


def test_source_neutral_occurrences_are_visible_without_a_repository():
    from datetime import datetime, timezone
    from dataclasses import replace
    from src.core.memory import Event, Source, Actor

    scope = Scope.from_mapping({"workspace": "team"})
    memory = InMemoryMemory()
    occurrence = Event(
        "meeting-1",
        "calendar.meeting",
        Source(
            "calendar",
            "account",
            "calendar-1",
            "https://calendar.example/meeting-1",
            "https://calendar.example/calendar-1",
        ),
        when=datetime(2026, 4, 20, tzinfo=timezone.utc),
        what="Discuss release",
        title="Release",
        actors=[Actor("user-1", "Alex", role="organiser")],
        valence=1,
        importance=3,
    )
    memory.record(scope, [occurrence])
    memory.record(scope, [occurrence])
    (view,) = memory.events(scope).events
    assert view.what == occurrence.what
    assert view.title == occurrence.title
    assert view.actors[0].name == "Alex"
    assert view.source.system == "calendar"
    assert view.source.url == occurrence.source.url
    assert view.source.container_url == occurrence.source.container_url
    assert (view.valence, view.importance) == (1, 3)
    memory.record(
        scope,
        [
            replace(
                occurrence,
                source=replace(occurrence.source, installation="another-account"),
            )
        ],
    )
    assert len(memory.events(scope).events) == 2
    assert not memory.events(Scope.from_mapping({"workspace": "other"})).events


def test_subject_parent_must_be_in_the_occurrence():
    from src.core.memory import Event, Subject, Source

    memory = InMemoryMemory()
    occurrence = Event(
        id="entry",
        type="calendar.meeting",
        source=Source(system="calendar", installation="account", container="calendar"),
        subjects=[Subject(id="child", type="agenda.item", parent="missing")],
    )
    with pytest.raises(
        ValueError, match="Subject 'child' has missing parent 'missing'"
    ):
        memory.record(Scope(), [occurrence])
    assert not memory._events
    assert not memory.entities


@pytest.mark.parametrize("path", ["repository", "pr_155"])
def test_path_names_do_not_overwrite_place_subjects(path):
    from copy import deepcopy
    from hashlib import sha256
    from src.integrations.github.memory_source import Origin, translate

    rows = json.loads(
        (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
    )
    row = deepcopy(
        next(row for row in rows if row["type"] == "pull_request_review_comment")
    )
    row["payload"]["comment"]["path"] = path
    occurrences = translate(
        Origin("installation", "922971929"), row["type"], row["action"], row["payload"]
    )
    memory = InMemoryMemory()
    memory.bind = lambda *args: "captured"
    scope = Scope.from_mapping(
        {"repository": row["payload"]["repository"]["full_name"]}
    )
    memory.record(scope, occurrences)
    expected = "sb_" + sha256(f"captured\0path\0{path}".encode()).hexdigest()[:24]
    assert memory.entities[expected]["type"] == "code.path"
    repository = next(
        entity
        for entity in memory.entities.values()
        if entity["type"] == "github.repository"
    )
    pull = next(
        entity
        for entity in memory.entities.values()
        if entity["type"] == "github.pull_request"
    )
    assert pull["parent"] == repository["id"]
    comment = next(
        event
        for event in memory.events(scope).events
        if event.type == "github.pull_request.review_comment"
    )
    assert any(
        subject.name == path and subject.type == "code.path"
        for subject in comment.subjects
    )
    assert comment.source.container == str(row["payload"]["repository"]["id"])


def test_adapter_modules_do_not_import_integrations_or_knowledge():
    import ast
    import src.core.memory as memory

    for path in Path(memory.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(
                    ("src.integrations", "src.core.knowledge")
                ), path
            elif isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith(("src.integrations", "src.core.knowledge"))
                    for alias in node.names
                ), path

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.core.memory; assert not any(name.startswith(('src.integrations', 'src.core.knowledge')) for name in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_ingestion_registry_is_bounded_and_keeps_recent_adapters(monkeypatch):
    from src.integrations.github import memory_history

    monkeypatch.setattr(
        memory_history, "_ingestions", memory_history._LRU(memory_history.MAX_ADAPTERS)
    )
    adapters = [InMemoryMemory() for _ in range(memory_history.MAX_ADAPTERS)]
    first = memory_history.ingestion(adapters[0])
    for adapter in adapters[1:]:
        memory_history.ingestion(adapter)
    assert memory_history.ingestion(adapters[0]) is first
    newest = InMemoryMemory()
    memory_history.ingestion(newest)
    assert len(memory_history._ingestions) == memory_history.MAX_ADAPTERS
    assert id(adapters[1]) not in memory_history._ingestions
    assert id(adapters[0]) in memory_history._ingestions
    assert id(newest) in memory_history._ingestions


def test_github_package_import_does_not_load_memory_or_services():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.integrations.github; assert 'src.core.memory' not in sys.modules; assert 'src.core.services' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "changes",
    [
        {"what": " "},
        {"when": datetime(2026, 4, 19, 14, 2, 11)},
        {"when": "2026-04-19T14:02:11Z"},
        {"ended_at": datetime(2026, 4, 19, 14, 2, 11)},
        {"valence": 3},
        {"importance": 0},
        {"importance": 6},
    ],
)
def test_record_validates_events_from_every_source(changes):
    from dataclasses import replace

    memory = InMemoryMemory()
    event = Event(
        id="observation",
        type="sourceant.observation",
        source=Source("calendar", "account", "calendar"),
        when=datetime(2026, 4, 19, 14, 2, 11, tzinfo=timezone.utc),
        what="Retry succeeded",
    )
    with pytest.raises(ValueError):
        memory.record(Scope(), [replace(event, **changes)])
    assert memory._events == {}


def test_record_returns_one_event_type_and_ignores_read_extras_on_write():
    from dataclasses import replace
    from datetime import datetime, timezone

    memory = InMemoryMemory()
    event = Event(
        id="observation",
        type="sourceant.observation",
        source=Source("sourceant", "local", "repo"),
        when=datetime(2026, 4, 19, 14, 2, 11, tzinfo=timezone.utc),
        what="Retry succeeded",
        actors=[Actor(id="ana", name="Ana")],
        subjects=[Subject(id="retry.py", type="code.path", name="retry.py")],
        valence=1,
    )
    (stored,) = memory.record(Scope(), [event])
    assert isinstance(stored, Event)
    assert stored.id in memory._events
    assert stored.actors[0].id in memory.entities
    assert stored.subjects[0].id in memory.entities
    (again,) = memory.record(Scope(), [replace(stored, score=0.9, outside_window=True)])
    assert again.id == stored.id
    assert again.score == 0.0
    assert not again.outside_window
    assert len(memory._events) == 1


def test_disabled_memory_has_only_empty_event_operations():
    from src.core.memory import DisabledMemory, EventPage

    memory = DisabledMemory()
    assert not memory.configured
    assert memory.record(Scope(), []) == []
    assert memory.events(Scope()) == EventPage()
    assert memory.recall(Scope(), "retry") == []


@pytest.mark.parametrize("dimension", ["workspace", "branch", "environment"])
def test_captured_delivery_is_isolated_between_scopes(dimension):
    from src.integrations.github.memory_source import Origin, translate

    rows = json.loads(
        (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
    )
    row = next(row for row in rows if row["type"] == "pull_request_review_comment")
    occurrences = translate(
        Origin("installation", "922971929"),
        row["type"],
        row["action"],
        row["payload"],
    )
    memory = InMemoryMemory()
    first = Scope.from_mapping({dimension: "A"})
    other = Scope.from_mapping({dimension: "B"})
    written = memory.record(first, occurrences)
    assert memory.events(other).events == []
    assert memory.recall(other, written[-1].what) == []
    repeated = memory.record(other, occurrences)
    assert {event.id for event in written}.isdisjoint(event.id for event in repeated)
    assert len(memory.events(first).events) == len(written)
    assert len(memory.events(other).events) == len(repeated)
    assert memory.recall(first, written[-1].what)
    assert memory.recall(other, repeated[-1].what)
    before = deepcopy(memory._events)
    memory.record(first, occurrences)
    memory.record(other, occurrences)
    assert memory._events == before
    for scope in (first, other):
        for view in memory.events(scope).events:
            assert view.source.container == "922971929"
            assert view.source.native_id in {
                event.source.native_id for event in occurrences
            }
    records = list(memory._events.values())
    identities = {
        (
            event["bucket"],
            event["source"]["system"],
            event["source"]["installation"],
            event["source"]["native_id"],
        )
        for event in records
    }
    assert len(identities) == len(records)
    assert {event["bucket"] for event in records} == {
        memory.bucket(first),
        memory.bucket(other),
    }


def test_webhook_delivery_can_be_recorded_for_two_workspaces(monkeypatch):
    from copy import deepcopy
    from src.integrations.github import memory_history

    row = next(
        row
        for row in json.loads(
            (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
        )
        if row["type"] == "pull_request_review_comment"
    )
    memory = InMemoryMemory()
    monkeypatch.setattr(memory_history, "memory_services", lambda: memory)
    scopes = []
    for workspace in ("first", "second"):
        payload = deepcopy(row["payload"])
        payload["sourceant_workspace_id"] = workspace
        payload["sourceant_delivery_id"] = "same-captured-delivery"
        scope = Scope.from_mapping(
            {
                "workspace": workspace,
                "repository": payload["repository"]["full_name"],
            }
        )
        scopes.append(scope)
        memory_history.record_webhook(row["type"], payload, stored=False, strict=True)
        assert memory.events(scope).events
    first, second = (memory.events(scope).events for scope in scopes)
    assert {event.id for event in first}.isdisjoint(event.id for event in second)


def test_in_memory_capture_identity_includes_bucket():
    from dataclasses import replace

    memory = InMemoryMemory()
    original = EventRecord.from_dict(example("event.review-comment.json"))
    memory._write_records([original], [])
    memory._write_records([original], [])
    memory._write_records([replace(original, id="different")], [])
    assert list(memory._events) == [original.id]
    memory._write_records([replace(original, bucket="other", id="other")], [])
    assert len(memory._events) == 2


def test_one_captured_github_event_in_two_workspaces():
    from src.integrations.github.memory_source import Origin, translate

    row = next(
        row
        for row in json.loads(
            (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
        )
        if row["type"] == "pull_request"
    )
    occurrences = translate(
        Origin("installation", "922971929"),
        row["type"],
        row["action"],
        row["payload"],
    )
    assert len(occurrences) == 1
    memory = InMemoryMemory()
    scopes = [Scope.from_mapping({"workspace": value}) for value in ("first", "second")]
    for scope in scopes:
        memory.record(scope, occurrences)
        memory.record(scope, occurrences)
    assert len(memory._events) == 2
    ids = []
    for scope in scopes:
        (view,) = memory.events(scope).events
        ids.append(view.id)
        assert view.source == occurrences[0].source
    assert ids[0] != ids[1]
