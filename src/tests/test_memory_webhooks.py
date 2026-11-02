import hashlib
import hmac
import json
from copy import deepcopy
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.core.memory import EventRecorder, InMemoryMemory
from src.core.memory import history
from src.core.services import service_registry
from src.tests.base_test import BaseTestCase

FIXTURE = Path(__file__).parent / "fixtures" / "github" / "pull-155-webhooks.json"
SECRET = "memory-webhook-test-secret"


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
        monkeypatch.setattr(history, "_heads", {})
        monkeypatch.setattr("src.api.routes.pr.GITHUB_SECRET", SECRET)
        monkeypatch.setattr(
            "src.controllers.repository_event_controller.dispatcher.dispatch",
            lambda event: None,
        )
        self.memory = InMemoryMemory()
        service_registry.register(EventRecorder, self.memory, "test")
        yield
        engine.dispose()

    def deliver(self, row):
        body = json.dumps(row["payload"]).encode()
        return self.client.post(
            "/api/prs/github-webhook",
            content=body,
            headers={
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
        events = list(self.memory.events.values())
        parents = [event for event in events if event["type"] == "github.pull_request"]
        assert len(parents) == 1
        parent = parents[0]
        assert parent["id"] == "ev_gh_922971929_pr_155"
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
        before = deepcopy(self.memory.events)
        before_entities = deepcopy(self.memory.entities)
        history._heads.clear()
        result = CliRunner().invoke(cli, ["memory-replay", "sourceant/sourceant"])
        assert result.exit_code == 0, result.output
        assert self.memory.events == before
        assert self.memory.entities == before_entities
        assert all(self.deliver(row).status_code == 201 for row in rows)
        assert self.memory.events == before

    def test_stateless_deliveries_still_record_events(self, monkeypatch):
        monkeypatch.setattr(
            "src.controllers.repository_event_controller.STATELESS_MODE", True
        )
        rows = json.loads(FIXTURE.read_text())
        for row in rows:
            assert self.deliver(row).status_code == 201
        assert any(
            event["type"] == "github.pull_request"
            for event in self.memory.events.values()
        )
        assert any(event.get("part_of") for event in self.memory.events.values())

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
        expected = f"ev_gh_922971929_review_{review['payload']['review']['id']}"
        assert expected in {
            item["record"]["id"] for item in result.structuredContent["results"]
        }


def test_translation_skips_payloads_without_repository():
    from src.core.memory.github import Bound, translate

    assert (
        translate(Bound("bucket", "installation", "container"), "push", None, {})
        is None
    )
