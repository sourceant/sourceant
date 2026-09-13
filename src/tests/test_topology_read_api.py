"""Reading a repository for connections, over the same HTTP a caller uses."""

import json
import os
import time
import uuid

import jwt
import pytest
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes.topology import get_topology_repository
from src.core.topology import SQLTopologyRepository
from src.core.topology.proposing import PROPOSE
from src.tests.base_test import BaseTestCase

TEST_JWT_SECRET = "topology-read-api-test-secret"

CLIENT = """import httpx


def charge(amount):
    return httpx.post("https://billing.internal/charges", json={"amount": amount})
"""


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
    """Proposes one connection, then stops."""

    def __init__(self, **overrides):
        self.arguments = {
            "target": "billing",
            "kind": "consumes",
            "path": "src/billing.py",
            "start_line": 5,
            "end_line": 5,
            "quote": 'httpx.post("https://billing.internal/charges"',
            "because": "It posts charges to billing.",
            **overrides,
        }
        self.spent = False

    def ask_with_tools(self, messages, tools, *, purpose="", require=False):
        if self.spent:
            return {"content": "done", "tool_calls": []}
        self.spent = True
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "name": PROPOSE,
                    "arguments": json.dumps(self.arguments),
                }
            ],
        }


class TestReadingForConnections(BaseTestCase):
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
            lambda user: {"acme/checkout"},
        )
        monkeypatch.setattr(
            "src.api.routes.topology.contents_reader",
            lambda repository, token, revision="": (
                lambda path: CLIENT if path == "src/billing.py" else None
            ),
        )
        self.model = Model()
        monkeypatch.setattr(
            "src.api.routes.topology.provider_for", lambda configuration: self.model
        )
        yield
        app.dependency_overrides.pop(get_topology_repository, None)

    def entity(self, identifier, name=""):
        return self.client.put(
            "/api/topology/entities",
            json={
                "id": identifier,
                "kind": "service",
                "status": "approved",
                "properties": {"name": name or identifier},
            },
            headers=self.headers,
        )

    def read(self, **overrides):
        return self.client.post(
            "/api/topology/read",
            json={
                "entity_id": "checkout",
                "repository": "acme/checkout",
                "targets": ["billing"],
                **overrides,
            },
            headers=self.headers,
        )

    def test_a_connection_read_from_the_code_is_recorded_as_pending(self):
        assert self.entity("checkout").status_code == 200
        assert self.entity("billing").status_code == 200

        answered = self.read().json()["data"]

        assert len(answered["proposed"]) == 1
        one = answered["proposed"][0]
        assert (one["source_id"], one["target_id"], one["type"]) == (
            "checkout",
            "billing",
            "consumes",
        )
        assert one["status"] == "pending"
        assert one["properties"]["inferred_from"] == "reading"
        assert one["evidence"][0]["source"].endswith("src/billing.py#L5-L5")

    def test_what_was_read_is_there_to_be_looked_at_afterwards(self):
        self.entity("checkout")
        self.entity("billing")

        self.read()
        standing = self.client.post(
            "/api/topology/traverse",
            json={"entity_ids": ["checkout"], "relationship_statuses": ["pending"]},
            headers=self.headers,
        ).json()["data"]

        assert [one["target_id"] for one in standing["relationships"]] == ["billing"]

    def test_a_quote_the_file_does_not_carry_records_nothing(self):
        self.entity("checkout")
        self.entity("billing")
        self.model.arguments["quote"] = 'httpx.post("https://ledger.internal/")'

        answered = self.read().json()["data"]

        assert answered["proposed"] == []

    def test_a_preview_leaves_the_graph_alone(self):
        self.entity("checkout")
        self.entity("billing")

        answered = self.read(persist=False).json()["data"]
        standing = self.client.post(
            "/api/topology/traverse",
            json={"entity_ids": ["checkout"], "relationship_statuses": ["pending"]},
            headers=self.headers,
        ).json()["data"]

        assert len(answered["proposed"]) == 1
        assert standing["relationships"] == []

    def test_a_system_nobody_recorded_cannot_be_named(self):
        self.entity("checkout")

        answered = self.read(targets=["billing"]).json()["data"]

        assert answered["proposed"] == []

    def test_a_repository_outside_the_workspace_is_refused(self):
        self.entity("checkout")
        self.entity("billing")

        answered = self.read(repository="someone/else")

        assert answered.status_code == 403

    def test_reading_for_an_entity_nobody_recorded_is_refused(self):
        self.entity("billing")

        answered = self.read()

        assert answered.status_code == 422
