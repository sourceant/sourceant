import hashlib
import hmac
import json
from copy import deepcopy
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.core.memory import Memory, InMemoryMemory
from src.integrations.github import memory_history as history
from src.core.services import service_registry
from src.tests.base_test import BaseTestCase

FIXTURE = Path(__file__).parent / "fixtures" / "github" / "pull-155-webhooks.json"
SECRET = "memory-webhook-test-secret"


def gateway(workspace):
    import os
    import time

    import jwt

    token = jwt.encode(
        {
            "sub": "gateway",
            "exp": int(time.time()) + 300,
            "scope": {"workspace_id": workspace},
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class TestMemoryWebhooks(BaseTestCase):
    @pytest.fixture(autouse=True)
    def memory_store(self, monkeypatch, tmp_path):
        from sqlalchemy import MetaData, create_engine
        from src.models.repository_event import RepositoryEvent

        engine = create_engine(
            f"sqlite:///{tmp_path / 'events.db'}",
            connect_args={"check_same_thread": False},
        )
        table = RepositoryEvent.__table__.to_metadata(MetaData())
        table.c.action.nullable = True
        table.create(engine)
        monkeypatch.setattr("src.config.db.engine", engine)
        monkeypatch.setattr("src.config.db.STATELESS_MODE", False)
        monkeypatch.setattr(
            "src.controllers.repository_event_controller.STATELESS_MODE", False
        )
        monkeypatch.setattr(service_registry, "_registrations", {})
        monkeypatch.setattr("src.api.routes.pr.GITHUB_SECRET", SECRET)
        monkeypatch.setattr(
            "src.controllers.repository_event_controller.dispatcher.dispatch",
            lambda event: None,
        )
        self.memory = InMemoryMemory()
        service_registry.register(Memory, self.memory, "test")
        yield
        engine.dispose()

    def deliver(self, row, delivery_id=None, workspace=None):
        body = json.dumps(row["payload"]).encode()
        return self.client.post(
            "/api/prs/github-webhook",
            content=body,
            headers={
                **({"X-GitHub-Delivery": delivery_id} if delivery_id else {}),
                **(gateway(workspace) if workspace else {}),
                "Content-Type": "application/json",
                "X-GitHub-Event": row["type"],
                "X-Hub-Signature-256": "sha256="
                + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
            },
        )

    def test_captured_webhooks_and_replay_produce_identical_records(self):
        from src.cli.main import cli

        rows = json.loads(FIXTURE.read_text())
        for row in rows:
            response = self.deliver(row)
            assert response.status_code == 201, response.text
        events = list(self.memory._events.values())
        parents = [event for event in events if event["type"] == "github.pull_request"]
        assert len(parents) == 1
        parent = parents[0]
        assert parent["id"].startswith("ev_gh_922971929_")
        assert parent["id"].endswith("_pr_155")
        parts = [event for event in events if event.get("part_of") == parent["id"]]
        assert (
            len(
                [
                    event
                    for event in parts
                    if event["type"] == "github.pull_request.review"
                ]
            )
            == 3
        )
        assert (
            len(
                [
                    event
                    for event in parts
                    if event["type"] == "github.pull_request.review_comment"
                ]
            )
            == 3
        )
        assert len([event for event in parts if event["type"] == "github.push"]) == 1
        assert (
            len(
                [
                    event
                    for event in parts
                    if event["type"]
                    in {"github.pull_request.closed", "github.pull_request.merged"}
                ]
            )
            == 1
        )
        main = [
            event
            for event in events
            if event.get("attributes", {}).get("github.ref") == "refs/heads/main"
        ]
        assert len(main) == 1 and "part_of" not in main[0]
        before_open = next(
            event
            for event in events
            if event["type"] == "github.push"
            and event["occurred_at"] == "2026-09-10T05:16:50+01:00"
        )
        assert "part_of" not in before_open
        before = deepcopy(self.memory._events)
        before_entities = deepcopy(self.memory.entities)
        history.ingestion(self.memory).heads.clear()
        result = CliRunner().invoke(cli, ["memory-replay", "sourceant/sourceant"])
        assert result.exit_code == 0, result.output
        assert self.memory._events == before
        assert self.memory.entities == before_entities
        assert all(self.deliver(row).status_code == 201 for row in rows)
        assert self.memory._events == before

    def test_one_delivery_to_two_workspaces_is_kept_in_each(self, monkeypatch):
        from src.core.scope import Scope

        monkeypatch.setenv("JWT_SECRET", "memory-webhook-gateway-secret")
        row = next(
            row
            for row in json.loads(FIXTURE.read_text())
            if row["type"] == "pull_request_review_comment"
        )
        for workspace in ("first", "second"):
            for _ in range(2):
                response = self.deliver(row, "same-delivery", workspace)
                assert response.status_code == 201, response.text
        repository = row["payload"]["repository"]
        views = [
            self.memory.events(
                Scope.from_mapping(
                    {"workspace": workspace, "repository": repository["full_name"]}
                )
            ).events
            for workspace in ("first", "second", "third")
        ]
        first, second, third = views
        assert first and second and not third
        assert len(first) == len(second)
        assert {event.id for event in first}.isdisjoint(event.id for event in second)
        for event in first + second:
            assert event.source.system == "github"
            assert event.source.container == str(repository["id"])

    def test_webhooks_and_knowledge_changes_share_the_repository_bucket(
        self, monkeypatch
    ):
        from src.core.knowledge import KnowledgeObject
        from src.core.knowledge.models import KnowledgeChange
        from src.core.knowledge.recording import event_for_change
        from src.core.memory import Actor
        from src.core.scope import Scope

        monkeypatch.setenv("JWT_SECRET", "memory-webhook-gateway-secret")
        row = next(
            row for row in json.loads(FIXTURE.read_text()) if row["type"] == "push"
        )
        repository = row["payload"]["repository"]["full_name"]
        assert self.deliver(row, "shared-bucket", "team").status_code == 201
        read_as_plugin = Scope.from_mapping(
            {"provider": "github", "repository": repository, "workspace": "team"}
        )
        change = KnowledgeChange(
            "knowledge.captured",
            Actor(id="ana", name="ana"),
            after=KnowledgeObject(
                id="retries", type="decision", status="active", summary="Retry twice"
            ),
        )
        self.memory.record(read_as_plugin, [event_for_change(read_as_plugin, change)])

        events = self.memory.events(read_as_plugin).events
        assert {event.type for event in events} == {"github.push", "knowledge.captured"}
        knowledge = next(e for e in events if e.type == "knowledge.captured")
        assert knowledge.source.container == repository
        assert not self.memory.events(
            read_as_plugin.extend({"workspace": "other"})
        ).events

    def test_push_only_repository_remains_visible(self):
        from src.core.scope import Scope

        row = next(
            row for row in json.loads(FIXTURE.read_text()) if row["type"] == "push"
        )
        repository = row["payload"]["repository"]["full_name"]
        scope = Scope.from_mapping({"repository": repository})
        assert self.deliver(row, "push-only-first").status_code == 201
        assert self.deliver(row, "push-only-repeat").status_code == 201
        recorded = set(self.memory._events)
        assert recorded
        page = self.memory.events(scope)
        assert {event.id for event in page.events} == recorded
        assert all(event.type == "github.push" for event in page.events)
        assert not history._stored(repository, type="pull_request")
        assert self.memory.recall(scope, page.events[0].what)[0].id in recorded
        assert not self.memory.events(scope.extend({"workspace": "other"})).events

    def test_history_resolves_timeline_and_missing_parents(self):
        from src.core.scope import Scope

        rows = json.loads(FIXTURE.read_text())
        for row in rows:
            assert self.deliver(row).status_code == 201
        scope = Scope.from_mapping({"repository": "sourceant/sourceant"})
        page = self.memory.events(scope)
        comments = [
            e for e in page.events if e.type == "github.pull_request.review_comment"
        ]
        assert comments
        parent = next(e for e in page.events if e.type == "github.pull_request")
        repository = rows[0]["payload"]["repository"]
        assert all(
            event.title == parent.title
            for event in page.events
            if event.part_of == parent.id
        )
        for comment in comments:
            captured = next(
                row["payload"]["comment"]
                for row in rows
                if row["type"] == "pull_request_review_comment"
                and str(row["payload"]["comment"]["id"])
                == self.memory._events[comment.id]["source"]["native_id"].split(":")[-1]
            )
            assert comment.source.url == captured["html_url"]
            assert comment.actors[0].type == (
                "bot" if captured["user"]["type"] == "Bot" else "person"
            )
            assert comment.part_of == parent.id
            assert (
                comment.title
                == parent.title
                == next(
                    row["payload"]["pull_request"]["title"]
                    for row in rows
                    if row["payload"].get("pull_request")
                )
            )
            assert comment.actors
            assert all(
                actor.name and actor.image and actor.url for actor in comment.actors
            )
            assert comment.source.system == "github"
            assert comment.source.container == str(repository["id"])
            assert (
                comment.source.url == self.memory._events[comment.id]["source"]["url"]
            )
            assert comment.source.container_url == repository["html_url"]
            assert any(subject.type == "code.path" for subject in comment.subjects)
            subjects = {subject.id: subject for subject in comment.subjects}
            assert any(
                subject.parent in subjects
                and subjects[subject.parent].name == repository["full_name"]
                for subject in comment.subjects
            )
        assert any(
            actor.type == "bot" for event in page.events for actor in event.actors
        )
        assert all(
            entity["type"] in {"person", "bot"}
            for entity in self.memory.entities.values()
            if entity.get("class") == "actor"
        )
        window = self.memory.events(scope, since=comments[0].when.isoformat())
        fetched = next(e for e in window.events if e.id == parent.id)
        assert fetched.outside_window
        assert all(not e.outside_window for e in window.events if e.id != parent.id)
        comments[0].attributes["changed"] = True
        assert "changed" not in self.memory._events[comments[0].id].get(
            "attributes", {}
        )

    def test_delivery_id_is_idempotent(self):
        rows = json.loads(FIXTURE.read_text())
        row = next(row for row in rows if row["action"] == "opened")
        body = json.dumps(row["payload"]).encode()
        headers = {
            "X-GitHub-Event": row["type"],
            "X-GitHub-Delivery": "captured-delivery",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256="
            + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
        }
        assert (
            self.client.post(
                "/api/prs/github-webhook", content=body, headers=headers
            ).status_code
            == 201
        )
        before = deepcopy(self.memory._events)
        assert (
            self.client.post(
                "/api/prs/github-webhook", content=body, headers=headers
            ).status_code
            == 201
        )
        assert self.memory._events == before
        assert ("", "sourceant/sourceant", "captured-delivery") in history.ingestion(
            self.memory
        ).deliveries

    def test_unhashable_memory_implementation_records_webhooks(self):
        class UnhashableMemory:
            __hash__ = None

            def __init__(self, store):
                self.store = store

            def __eq__(self, other):
                return self is other

            def __getattr__(self, name):
                return getattr(self.store, name)

        memory = UnhashableMemory(self.memory)
        service_registry.unregister_provider("test")
        service_registry.register(Memory, memory, "test")
        rows = json.loads(FIXTURE.read_text())
        row = next(row for row in rows if row["action"] == "opened")
        assert self.deliver(row).status_code == 201
        assert memory._events
        assert history.ingestion(memory).deliveries

    def test_ingestion_caches_are_bounded_and_keep_recent_entries(self, monkeypatch):
        monkeypatch.setattr(history, "MAX_DELIVERIES", 3)
        monkeypatch.setattr(history, "MAX_HEADS", 2)
        row = deepcopy(
            next(
                row
                for row in json.loads(FIXTURE.read_text())
                if row["action"] == "opened"
            )
        )
        original_branch = row["payload"]["pull_request"]["head"]["ref"]
        for index in range(3):
            row["payload"]["pull_request"]["head"]["ref"] = f"branch-{index}"
            assert self.deliver(row, f"delivery-{index}").status_code == 201
        state = history.ingestion(self.memory)
        container = str(row["payload"]["repository"]["id"])
        assert len(state.deliveries) == 3
        assert list(state.heads) == [(container, "branch-1"), (container, "branch-2")]
        assert state.heads.get((container, "branch-1"))
        assert self.deliver(row, "delivery-0").status_code == 201
        row["payload"]["pull_request"]["head"]["ref"] = "branch-3"
        assert self.deliver(row, "delivery-3").status_code == 201
        assert list(state.deliveries) == [
            ("", row["payload"]["repository"]["full_name"], delivery)
            for delivery in ["delivery-2", "delivery-0", "delivery-3"]
        ]
        assert list(state.heads) == [(container, "branch-1"), (container, "branch-3")]

        pushes = [
            entry
            for entry in json.loads(FIXTURE.read_text())
            if entry["type"] == "push"
            and entry["payload"]["ref"] == f"refs/heads/{original_branch}"
        ]
        push = deepcopy(pushes[-1])
        push["payload"]["ref"] = "refs/heads/branch-0"
        assert self.deliver(push, "delivery-push").status_code == 201
        pushed = next(
            event
            for event in self.memory._events.values()
            if event["type"] == "github.push"
        )
        assert pushed["part_of"] == next(
            event["id"]
            for event in self.memory._events.values()
            if event["type"] == "github.pull_request"
        )
        assert len(state.heads) == 2
        assert len(state.deliveries) == 3

    def test_stateless_deliveries_still_record_events(self, monkeypatch):
        monkeypatch.setattr(
            "src.controllers.repository_event_controller.STATELESS_MODE", True
        )
        rows = json.loads(FIXTURE.read_text())
        for row in rows:
            assert self.deliver(row).status_code == 201
        assert any(
            event["type"] == "github.pull_request"
            for event in self.memory._events.values()
        )
        assert any(event.get("part_of") for event in self.memory._events.values())

    def test_recording_failure_does_not_fail_the_webhook(self, monkeypatch, caplog):
        def fail(events, entities):
            raise RuntimeError("Memory is unavailable")

        monkeypatch.setattr(self.memory, "record", fail)
        opened = next(
            row for row in json.loads(FIXTURE.read_text()) if row["action"] == "opened"
        )
        assert self.deliver(opened).status_code == 201
        assert "Could not record" in caplog.text

    @pytest.mark.asyncio
    async def test_scoped_mcp_recall_finds_github_events(self):
        from mcp.shared.memory import create_connected_server_and_client_session
        from src.core.context import DefaultContextProvider
        from src.core.mcp import create_mcp_server

        review = next(
            row
            for row in json.loads(FIXTURE.read_text())
            if row["type"] == "pull_request_review"
            and row["payload"]["review"].get("body")
        )
        assert self.deliver(review).status_code == 201
        server = create_mcp_server(DefaultContextProvider(), services=service_registry)
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool(
                "recall",
                {
                    "scope": {"repository": "sourceant/sourceant"},
                    "query": review["payload"]["review"]["body"].strip(),
                    "limit": 100,
                },
            )
        assert not result.isError, result
        expected = next(
            event["id"]
            for event in self.memory._events.values()
            if event["type"] == "github.pull_request.review"
            and event["id"].endswith(f"_review_{review['payload']['review']['id']}")
        )
        assert expected in {item["id"] for item in result.structuredContent["results"]}


def test_translation_skips_payloads_without_repository():
    from src.integrations.github.memory_source import Origin, translate

    assert translate(Origin("installation", "container"), "push", None, {}) is None
