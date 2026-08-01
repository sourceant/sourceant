import logging
import os
import time
import uuid

import jwt
import pytest
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes import topology as topology_routes
from src.api.routes.topology import get_topology_repository
from src.core.services import service_registry
from src.core.topology import (
    InMemoryTopologyRepository,
    SQLTopologyRepository,
    TopologyRepository,
)
from src.tests.base_test import BaseTestCase

TEST_JWT_SECRET = "topology-api-test-secret"


def _token(workspace_id: str, repository_ids=None) -> str:
    return jwt.encode(
        {
            "sub": "1",
            "username": "octocat",
            "scope": {
                "workspace_id": workspace_id,
                "repository_ids": repository_ids or [],
            },
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


@pytest.fixture
def empty_registry():
    """Isolate the global registry so provider tests do not depend on plugin load order."""
    saved = dict(service_registry._registrations)
    service_registry._registrations.clear()
    yield service_registry
    service_registry._registrations.clear()
    service_registry._registrations.update(saved)


def test_topology_repository_prefers_a_registered_provider(empty_registry):
    provided = InMemoryTopologyRepository()
    empty_registry.register(TopologyRepository, provided, "test")

    assert get_topology_repository() is provided


def test_topology_repository_falls_back_when_no_plugin_provides_one(
    empty_registry, monkeypatch
):
    monkeypatch.setattr(topology_routes, "_fallback", None)
    monkeypatch.setattr(topology_routes, "get_engine", lambda: None)

    resolved = get_topology_repository()

    assert isinstance(resolved, InMemoryTopologyRepository)
    assert get_topology_repository() is resolved


class TestTopologyApi(BaseTestCase):
    """Drives /api/topology the way the gateway does: real HTTP, real service JWT."""

    @pytest.fixture(autouse=True)
    def workspace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
        self.workspace_id = uuid.uuid4().hex
        self.headers = {"Authorization": f"Bearer {_token(self.workspace_id)}"}
        repository = SQLTopologyRepository(
            create_engine(f"sqlite:///{tmp_path / 'topology.db'}"),
            create_schema=True,
        )
        app.dependency_overrides[get_topology_repository] = lambda: repository
        yield
        app.dependency_overrides.pop(get_topology_repository, None)

    def put_entity(self, identifier, headers=None, **overrides):
        return self.client.put(
            "/api/topology/entities",
            json={
                "id": identifier,
                "kind": "service",
                "status": "approved",
                **overrides,
            },
            headers=headers or self.headers,
        )

    def test_entities_and_relationships_round_trip_through_http(self):
        assert self.put_entity("checkout").status_code == 200
        assert self.put_entity("ledger").status_code == 200

        created = self.client.put(
            "/api/topology/relationships",
            json={
                "id": "checkout-ledger",
                "source_id": "checkout",
                "target_id": "ledger",
                "type": "depends_on",
                "status": "pending",
                "confidence": 0.6,
                "evidence": [{"id": "commit-1", "kind": "commit", "source": "github"}],
            },
            headers=self.headers,
        )
        listing = self.client.post(
            "/api/topology/search", json={}, headers=self.headers
        )
        traversal = self.client.post(
            "/api/topology/traverse",
            json={"entity_ids": ["checkout"]},
            headers=self.headers,
        )

        assert created.status_code == 200
        body = listing.json()["data"]
        assert [entity["id"] for entity in body["entities"]] == ["checkout", "ledger"]
        assert body["total"] == 2
        assert [edge["id"] for edge in body["relationships"]] == ["checkout-ledger"]
        walked = traversal.json()["data"]
        assert [entity["id"] for entity in walked["entities"]] == [
            "checkout",
            "ledger",
        ]
        assert walked["relationships"][0]["evidence"][0]["id"] == "commit-1"

    def test_another_workspace_cannot_read_or_link_the_graph(self):
        self.put_entity("checkout")
        self.put_entity("ledger")
        self.client.put(
            "/api/topology/relationships",
            json={
                "id": "checkout-ledger",
                "source_id": "checkout",
                "target_id": "ledger",
                "type": "depends_on",
                "status": "approved",
            },
            headers=self.headers,
        )
        intruder = {"Authorization": f"Bearer {_token(uuid.uuid4().hex)}"}

        listing = self.client.post("/api/topology/search", json={}, headers=intruder)
        traversal = self.client.post(
            "/api/topology/traverse",
            json={"entity_ids": ["checkout"]},
            headers=intruder,
        )
        borrowed = self.client.put(
            "/api/topology/relationships",
            json={
                "id": "stolen",
                "source_id": "checkout",
                "target_id": "ledger",
                "type": "depends_on",
                "status": "approved",
            },
            headers=intruder,
        )

        assert listing.json()["data"]["entities"] == []
        assert listing.json()["data"]["total"] == 0
        assert traversal.json()["data"]["entities"] == []
        assert borrowed.status_code == 422

    def test_search_filters_by_kind_status_and_properties(self):
        self.put_entity("checkout", kind="service", status="approved")
        self.put_entity("legacy", kind="service", status="rejected")
        self.put_entity(
            "orders-db",
            kind="datastore",
            status="approved",
            properties={"team": "payments"},
        )

        by_kind = self.client.post(
            "/api/topology/search", json={"kinds": ["datastore"]}, headers=self.headers
        )
        by_status = self.client.post(
            "/api/topology/search",
            json={"statuses": ["approved"]},
            headers=self.headers,
        )
        by_property = self.client.post(
            "/api/topology/search",
            json={"properties": {"team": "payments"}},
            headers=self.headers,
        )

        assert [e["id"] for e in by_kind.json()["data"]["entities"]] == ["orders-db"]
        assert sorted(e["id"] for e in by_status.json()["data"]["entities"]) == [
            "checkout",
            "orders-db",
        ]
        assert [e["id"] for e in by_property.json()["data"]["entities"]] == [
            "orders-db"
        ]

    def test_relationship_endpoints_reject_unknown_entities(self):
        self.put_entity("checkout")

        response = self.client.put(
            "/api/topology/relationships",
            json={
                "id": "dangling",
                "source_id": "checkout",
                "target_id": "nowhere",
                "type": "depends_on",
                "status": "pending",
            },
            headers=self.headers,
        )

        assert response.status_code == 422
        assert "does not exist in scope" in response.json()["detail"]

    def test_traversal_bounds_are_enforced(self):
        self.put_entity("checkout")

        too_deep = self.client.post(
            "/api/topology/traverse",
            json={"entity_ids": ["checkout"], "depth": 4},
            headers=self.headers,
        )
        empty_seed = self.client.post(
            "/api/topology/traverse",
            json={"entity_ids": []},
            headers=self.headers,
        )

        assert too_deep.status_code == 422
        assert empty_seed.status_code == 422

    def test_an_unreachable_store_is_reported_as_unavailable(self, caplog):
        """The graph driver raises a bare ValueError when it cannot connect."""

        class UnreachableStore(InMemoryTopologyRepository):
            def search(self, query):
                raise ValueError("Cannot resolve address memgraph:7687")

            def traverse(self, traversal):
                raise ValueError("Cannot resolve address memgraph:7687")

        app.dependency_overrides[get_topology_repository] = UnreachableStore

        with caplog.at_level(logging.ERROR):
            listing = self.client.post(
                "/api/topology/search", json={}, headers=self.headers
            )
            traversal = self.client.post(
                "/api/topology/traverse",
                json={"entity_ids": ["checkout"]},
                headers=self.headers,
            )

        assert listing.status_code == 503
        assert traversal.status_code == 503
        # The caller is told which subsystem failed, never which host it is on.
        assert listing.json()["detail"] == "Graph store unreachable"
        assert "memgraph:7687" not in listing.text
        assert "memgraph:7687" in caplog.text

    def test_a_query_the_caller_can_correct_is_still_rejected_as_invalid(self):
        response = self.client.post(
            "/api/topology/search", json={"limit": 500}, headers=self.headers
        )

        assert response.status_code == 422
        assert "limit" in response.json()["detail"]

    def test_a_token_without_a_workspace_scope_is_refused(self):
        unscoped = jwt.encode(
            {"sub": "1", "exp": int(time.time()) + 300},
            os.environ["JWT_SECRET"],
            algorithm="HS256",
        )

        response = self.client.post(
            "/api/topology/search",
            json={},
            headers={"Authorization": f"Bearer {unscoped}"},
        )

        assert response.status_code == 403

    def test_requests_without_a_token_are_refused(self):
        response = self.client.post("/api/topology/search", json={})

        assert response.status_code in (401, 422)

    def test_removing_an_entity_takes_its_relationships_with_it(self):
        for identifier in ("checkout", "ledger", "search"):
            self.put_entity(identifier)
        for edge, source, target in (
            ("a", "checkout", "ledger"),
            ("b", "search", "checkout"),
            ("c", "search", "ledger"),
        ):
            self.client.put(
                "/api/topology/relationships",
                json={
                    "id": edge,
                    "source_id": source,
                    "target_id": target,
                    "type": "depends_on",
                    "status": "approved",
                },
                headers=self.headers,
            )

        removed = self.client.delete(
            "/api/topology/entities/checkout", headers=self.headers
        )
        again = self.client.delete(
            "/api/topology/entities/checkout", headers=self.headers
        )
        listing = self.client.post(
            "/api/topology/search", json={}, headers=self.headers
        )

        assert removed.status_code == 200
        assert again.status_code == 404
        body = listing.json()["data"]
        assert [entity["id"] for entity in body["entities"]] == ["ledger", "search"]
        assert [edge["id"] for edge in body["relationships"]] == ["c"]

    def test_removing_a_relationship_keeps_both_endpoints(self):
        self.put_entity("checkout")
        self.put_entity("ledger")
        self.client.put(
            "/api/topology/relationships",
            json={
                "id": "edge",
                "source_id": "checkout",
                "target_id": "ledger",
                "type": "depends_on",
                "status": "approved",
            },
            headers=self.headers,
        )

        removed = self.client.delete(
            "/api/topology/relationships/edge", headers=self.headers
        )
        listing = self.client.post(
            "/api/topology/search", json={}, headers=self.headers
        )

        assert removed.status_code == 200
        body = listing.json()["data"]
        assert [entity["id"] for entity in body["entities"]] == ["checkout", "ledger"]
        assert body["relationships"] == []

    def test_another_workspace_cannot_remove_the_graph(self):
        self.put_entity("checkout")
        intruder = {"Authorization": f"Bearer {_token(uuid.uuid4().hex)}"}

        response = self.client.delete(
            "/api/topology/entities/checkout", headers=intruder
        )
        listing = self.client.post(
            "/api/topology/search", json={}, headers=self.headers
        )

        assert response.status_code == 404
        assert [e["id"] for e in listing.json()["data"]["entities"]] == ["checkout"]
