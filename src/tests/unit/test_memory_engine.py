import json
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
        assert view.source.container == repository["full_name"]
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
        memory.bind = lambda *args: "captured"
        memory.record(Scope.from_mapping({}), occurrences)
        assert memory._events == {event["id"]: event for event in original["events"]}
        assert memory.entities == {
            entity["id"]: entity for entity in original["entities"]
        }


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
    page = memory.events(scope, since="2026-01-01T00:00:00Z")
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
    assert memory.entities["pl_gh_922971929"]["type"] == "github.repository"
    assert memory.entities["pl_gh_922971929_pr_155"]["parent"] == "pl_gh_922971929"
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


def test_source_bindings_survive_a_fresh_http_adapter(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, func, select
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.migrations.versions import memory_source_bindings as migration
    from src.core.memory.bindings import source_bindings
    from src.core.memory import Event, Source

    engine = create_engine(f"sqlite:///{tmp_path / 'bindings.db'}")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    scope = Scope.from_mapping({"workspace": "team", "repository": "calendar/release"})
    occurrence = Event(
        id="meeting-1",
        type="calendar.meeting",
        source=Source(system="calendar", installation="account", container="release"),
        when=datetime(2026, 4, 20, tzinfo=timezone.utc),
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
            (view,) = fresh.events(scope).events
            assert view.what == occurrence.what
            assert fresh.recall(scope, "release")[0].id == view.id
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


def test_registered_resolver_preserves_history_for_old_deliveries(
    monkeypatch, tmp_path
):
    from sqlalchemy import create_engine
    from sqlmodel import Session
    from src.core.memory.bindings import source_bindings
    from src.models.repository_event import RepositoryEvent
    from src.core.services import memory_services, ServiceRegistry

    memory_services(ServiceRegistry())
    rows = json.loads(
        (FIXTURES.parent / "github" / "pull-155-webhooks.json").read_text()
    )
    row = next(row for row in rows if row["action"] == "opened")
    payload = row["payload"]
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    RepositoryEvent.__table__.create(engine)
    source_bindings.create(engine)
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    with Session(engine) as session:
        session.add(
            RepositoryEvent(
                provider="GitHub",
                type=row["type"],
                action=row["action"],
                repository_full_name=payload["repository"]["full_name"],
                payload=payload,
            )
        )
        session.commit()
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
        memory.events(
            Scope.from_mapping({"repository": payload["repository"]["full_name"]})
        )
    finally:
        memory.close()
        engine.dispose()
    assert any(
        source["system"] == "github"
        and source["container"] == str(payload["repository"]["id"])
        for source in calls
    )


def test_source_binding_is_durable_when_engine_write_fails(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from src.core.memory.bindings import source_bindings
    from src.core.memory import Event, Source

    engine = create_engine(f"sqlite:///{tmp_path / 'bindings.db'}")
    source_bindings.create(engine)
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    scope = Scope.from_mapping({"workspace": "team", "repository": "calendar/release"})
    occurrence = Event(
        id="meeting-1",
        type="calendar.meeting",
        source=Source(system="calendar", installation="account", container="release"),
        when=datetime(2026, 4, 20, tzinfo=timezone.utc),
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


def test_long_source_binding_roundtrip(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, func, select
    from src.core.memory.bindings import SourceBindings, source_bindings

    engine = create_engine(f"sqlite:///{tmp_path / 'long-bindings.db'}")
    source_bindings.create(engine)
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    scope = Scope.from_mapping(
        {"workspace": "team", "repository": "acme/shop", "branch": "long/" * 100}
    )
    source = Source.for_scope(scope)
    assert len(source.container) > 255
    SourceBindings().remember(scope, [source, source])
    SourceBindings().remember(scope, [source])
    assert SourceBindings().sources(scope) == [source]
    assert (
        SourceBindings().sources(Scope.from_mapping({"repository": "acme/shop"})) == []
    )
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(source_bindings)) == 1


def test_source_binding_digest_lookups_preserve_scope_values(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from src.core.memory.bindings import SourceBindings, source_bindings

    engine = create_engine(f"sqlite:///{tmp_path / 'scope-bindings.db'}")
    source_bindings.create(engine)
    monkeypatch.setattr("src.config.db.get_engine", lambda: engine)
    repository = "repo/" * 100
    sources = [
        Source("calendar", "account", "one"),
        Source("calendar", "account", "two"),
    ]
    for workspace, source in zip(("team/" * 100, "other"), sources):
        scope = Scope.from_mapping({"workspace": workspace, "repository": repository})
        SourceBindings().remember(scope, [source])
        assert SourceBindings().sources(scope) == [source]
    assert set(
        SourceBindings().sources(Scope.from_mapping({"repository": repository}))
    ) == set(sources)
    assert SourceBindings().sources(Scope.from_mapping({"repository": "other"})) == []


@pytest.mark.parametrize("legacy", [False, True])
def test_source_binding_schema_upgrade_and_downgrade(legacy):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.migrations.versions import memory_source_bindings as first
    from src.migrations.versions import memory_source_binding_keys as second
    from src.core.memory.bindings import binding_digest, source_bindings

    engine = sa.create_engine("sqlite://")
    values = dict(
        zip(first.DIMENSIONS, ("team", "acme/shop", "calendar", "a", "x" * 600))
    )
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            if legacy:
                old = sa.Table(
                    "memory_source_bindings",
                    sa.MetaData(),
                    *(
                        sa.Column(name, sa.String(255), primary_key=True)
                        for name in first.DIMENSIONS
                    ),
                )
                old.create(connection)
                connection.execute(old.insert().values(**values))
            else:
                first.upgrade()
            second.upgrade()
            inspector = sa.inspect(connection)
            assert inspector.get_pk_constraint("memory_source_bindings")[
                "constrained_columns"
            ] == ["id"]
            columns = {
                c["name"]: c["type"]
                for c in inspector.get_columns("memory_source_bindings")
            }
            assert all(isinstance(columns[name], sa.Text) for name in first.DIMENSIONS)
            assert inspector.get_indexes("memory_source_bindings")[0][
                "column_names"
            ] == ["repository_digest", "workspace_digest"]
            if legacy:
                row = connection.execute(sa.select(source_bindings)).mappings().one()
                assert all(row[name] == value for name, value in values.items())
                assert row["id"] == binding_digest(
                    *(values[name] for name in first.DIMENSIONS)
                )
                assert row["repository_digest"] == binding_digest(values["repository"])
            second.downgrade()
            assert sa.inspect(connection).has_table("memory_source_bindings")
            first.downgrade()
            assert not sa.inspect(connection).has_table("memory_source_bindings")
