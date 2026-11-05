import json
from pathlib import Path

import httpx
import pytest

from src.core.memory import TrailpadMemory, InMemoryMemory
from src.core.memory.engine import Entity, Event
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
        memory._write_records([Event.from_dict(event)], [Entity.from_dict(entity)])
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
        recalled = memory.recall(Scope.from_mapping({}), "retry").results[0]
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
            memory._write_records(
                [Event.from_dict(example("event.review-comment.json"))], []
            )
    finally:
        memory.close()


def test_in_memory_records_are_idempotent_and_defensively_copied():
    memory = InMemoryMemory()
    event = Event.from_dict(example("event.review-comment.json"))
    memory._write_records([event], [])
    memory._write_records([event], [])
    assert len(memory.events) == 1
    event.content[0]["text"] = "Changed"
    with pytest.raises(ValueError):
        memory._write_records([event], [])
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
    memory._write_records([first, second, foreign], [])
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
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        observation = Remembered(
            event["content"][0]["text"],
            datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")),
            why=event["why"],
        )
        result = memory.remember(
            Scope.from_mapping({"actor": actor["name"]}), observation
        )
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
    events = mapped.events
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
        memory.bind = lambda *args: "captured"
        memory.record(Scope.from_mapping({}), occurrences)
        assert memory.events == {event["id"]: event for event in original["events"]}
        assert memory.entities == {
            entity["id"]: entity for entity in original["entities"]
        }


def test_history_resolves_buckets_once_for_several_external_parents(monkeypatch):
    from dataclasses import replace

    memory = InMemoryMemory()
    scope = Scope.from_mapping({})
    bucket = memory.bucket(scope)
    original = Event.from_dict(example("event.review-comment.json"))
    parents = [
        replace(
            original,
            bucket=bucket,
            id=f"parent-{i}",
            occurred_at="2020-01-01T00:00:00Z",
            part_of="",
        )
        for i in range(3)
    ]
    children = [
        replace(original, bucket=bucket, id=f"child-{i}", part_of=parent.id)
        for i, parent in enumerate(parents)
    ]
    memory._write_records(parents + children, [])
    calls = []
    monkeypatch.setattr(
        memory, "buckets", lambda scope: calls.append(scope) or [bucket]
    )
    page = memory.history(scope, since="2026-01-01T00:00:00Z")
    assert sum(event.outside_window for event in page.events) == 3
    assert calls == [scope]


def test_github_source_does_not_import_adapter():
    import ast
    from src.integrations.github import memory_source

    tree = ast.parse(Path(memory_source.__file__).read_text())
    imports = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert [
        module for module in imports if module and module.startswith("src.core.memory")
    ] == ["src.core.memory.models"]
    assert not any(
        isinstance(node, ast.Import)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        and any(alias.name.startswith("src.core.memory") for alias in node.names)
    )


def test_source_neutral_occurrences_are_visible_without_a_repository():
    from datetime import datetime, timezone
    from dataclasses import replace
    from src.core.memory import Occurrence, Source, OccurrenceActor

    scope = Scope.from_mapping({"workspace": "team"})
    memory = InMemoryMemory()
    occurrence = Occurrence(
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
        actors=[OccurrenceActor("user-1", "Alex", role="organiser")],
        valence=1,
        importance=3,
    )
    memory.record(scope, [occurrence])
    memory.record(scope, [occurrence])
    (view,) = memory.history(scope).events
    assert view.what == occurrence.what
    assert view.title == occurrence.title
    assert view.actors[0].name == "Alex"
    assert view.source["system"] == "calendar"
    assert view.source["url"] == occurrence.source.url
    assert view.source["container_url"] == occurrence.source.container_url
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
    assert len(memory.history(scope).events) == 2
    assert not memory.history(Scope.from_mapping({"workspace": "other"})).events


def test_subject_parent_must_be_in_the_occurrence():
    from src.core.memory import Occurrence, OccurrenceSubject, Source

    memory = InMemoryMemory()
    occurrence = Occurrence(
        id="entry",
        type="calendar.meeting",
        source=Source(system="calendar", installation="account", container="calendar"),
        subjects=[OccurrenceSubject(id="child", type="agenda.item", parent="missing")],
    )
    with pytest.raises(
        ValueError, match="Subject 'child' has missing parent 'missing'"
    ):
        memory.record(Scope(), [occurrence])
    assert not memory.events
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
    assert memory.entities["pl_gh_922971929"]["type"] == "github.repository"
    assert memory.entities["pl_gh_922971929_pr_155"]["parent"] == "pl_gh_922971929"
    comment = next(
        event
        for event in memory.history(scope).events
        if event.type == "github.pull_request.review_comment"
    )
    assert any(
        subject.name == path and subject.type == "code.path"
        for subject in comment.subjects
    )
    assert comment.source["container"] == row["payload"]["repository"]["full_name"]


def test_adapter_modules_do_not_import_integrations():
    import ast
    import src.core.memory as memory

    for path in Path(memory.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("src.integrations"), path
            elif isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith("src.integrations") for alias in node.names
                ), path


def test_source_bindings_survive_a_fresh_http_adapter(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, func, select
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.migrations.versions import memory_source_bindings as migration
    from src.core.memory.bindings import source_bindings
    from src.core.memory import Occurrence, Source

    engine = create_engine(f"sqlite:///{tmp_path / 'bindings.db'}")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    scope = Scope.from_mapping({"workspace": "team", "repository": "calendar/release"})
    occurrence = Occurrence(
        id="meeting-1",
        type="calendar.meeting",
        source=Source(system="calendar", installation="account", container="release"),
        when="2026-04-20T00:00:00Z",
        what="Discuss release",
    )
    saved = {"events": [], "entities": []}
    from hashlib import sha256

    def handle(request):
        if request.method == "PUT":
            values = json.loads(request.content)["binding"]
            identity = tuple(
                values[name] for name in ("system", "installation", "container")
            )
            return httpx.Response(
                200,
                json={"id": "bk_" + sha256(repr(identity).encode()).hexdigest()[:24]},
            )
        if request.url.path == "/v1/records":
            saved.update(json.loads(request.content))
            return httpx.Response(204)
        if request.url.path == "/v1/events":
            assert saved["events"][0]["bucket"] in request.url.params.get_list("bucket")
            return httpx.Response(200, json={**saved, "next": None})
        if request.url.path == "/v1/recall":
            assert (
                saved["events"][0]["bucket"]
                in json.loads(request.content)["filters"]["buckets"]
            )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"record": event, "score": 1.0} for event in saved["events"]
                    ],
                    "entities": saved["entities"],
                },
            )
        raise AssertionError(request.url)

    try:
        first = TrailpadMemory(
            "https://memory.example",
            "test-service-key",
            transport=httpx.MockTransport(handle),
        )
        try:
            first.record(scope, [occurrence])
            first.record(scope, [occurrence])
        finally:
            first.close()
        with engine.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(source_bindings))
                == 1
            )
        fresh = TrailpadMemory(
            "https://memory.example",
            "test-service-key",
            transport=httpx.MockTransport(handle),
        )
        try:
            (view,) = fresh.history(scope).events
            assert view.what == occurrence.what
            assert fresh.recall(scope, "release").results[0].id == view.id
            assert (
                len(
                    fresh.buckets(
                        Scope.from_mapping(
                            {"workspace": "other", "repository": "calendar/release"}
                        )
                    )
                )
                == 1
            )
        finally:
            fresh.close()
    finally:
        engine.dispose()


def test_registered_resolver_preserves_history_for_old_deliveries(monkeypatch):
    from src.integrations.github import memory_history
    from src.models.repository_event import RepositoryEvent
    from src.core.services import memory_services, ServiceRegistry

    memory_services(ServiceRegistry())
    rows = json.loads(
        (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
    )
    row = next(row for row in rows if row["action"] == "opened")
    payload = row["payload"]
    monkeypatch.setattr(
        memory_history, "_stored", lambda repository: [RepositoryEvent(payload=payload)]
    )
    calls = []

    def handle(request):
        if request.method == "PUT":
            source = json.loads(request.content)["binding"]
            calls.append(source)
            return httpx.Response(200, json={"id": source["container"]})
        if request.url.path == "/v1/events":
            assert str(payload["repository"]["id"]) in request.url.params.get_list(
                "bucket"
            )
            return httpx.Response(
                200, json={"events": [], "entities": [], "next": None}
            )
        raise AssertionError(request.url)

    memory = TrailpadMemory(
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        memory.history(
            Scope.from_mapping({"repository": payload["repository"]["full_name"]})
        )
    finally:
        memory.close()
    assert any(
        source["system"] == "github"
        and source["container"] == str(payload["repository"]["id"])
        for source in calls
    )


def test_source_binding_is_durable_when_engine_write_fails(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from src.core.memory.bindings import source_bindings
    from src.core.memory import Occurrence, Source

    engine = create_engine(f"sqlite:///{tmp_path / 'bindings.db'}")
    source_bindings.create(engine)
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    scope = Scope.from_mapping({"workspace": "team", "repository": "calendar/release"})
    occurrence = Occurrence(
        id="meeting-1",
        type="calendar.meeting",
        source=Source(system="calendar", installation="account", container="release"),
        when="2026-04-20T00:00:00Z",
    )

    def handle(request):
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "captured"})
        assert request.url.path == "/v1/records"
        with engine.connect() as connection:
            row = connection.execute(select(source_bindings)).one()
            assert (
                row.workspace,
                row.repository,
                row.system,
                row.installation,
                row.container,
            ) == ("team", "calendar/release", "calendar", "account", "release")
        return httpx.Response(503)

    memory = TrailpadMemory(
        "https://memory.example",
        "test-service-key",
        transport=httpx.MockTransport(handle),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            memory.record(scope, [occurrence])
        with engine.connect() as connection:
            assert (
                connection.execute(select(source_bindings)).one().container == "release"
            )
    finally:
        memory.close()
        engine.dispose()


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


def test_memory_service_setup_registers_resolvers_idempotently(monkeypatch):
    from src.core.memory import Memory, bindings
    from src.core.services import ServiceRegistry, memory_services
    from src.integrations.github.memory_history import resolve_sources

    monkeypatch.setattr(bindings, "_resolvers", [])
    registry = ServiceRegistry()
    memory = InMemoryMemory()
    registry.register(Memory, memory, "test")
    assert memory_services(registry) is memory
    assert memory_services(registry) is memory
    assert bindings._resolvers == [resolve_sources]
