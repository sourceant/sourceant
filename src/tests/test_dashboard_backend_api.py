import hashlib
import time
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.api.routes import artifacts, requirements, topology, usage
from src.core.knowledge import InMemoryKnowledgeRepository
from src.core.requirements import KnowledgeBackedRequirements, SQLRequirementsRepository
from src.core.scope import Scope
from src.core.storage import FileSystemArtifactStore
from src.core.topology import SQLTopologyRepository
from src.core.usage.reading import SQLUsageReader
from src.models.token_usage import TokenUsageRecord


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "dashboard-backend-test-secret")
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    knowledge = InMemoryKnowledgeRepository()
    store = KnowledgeBackedRequirements(
        SQLRequirementsRepository(engine, create_schema=True), knowledge
    )
    graph = SQLTopologyRepository(engine, create_schema=True)
    TokenUsageRecord.__table__.create(engine, checkfirst=True)
    app = FastAPI()
    for name, module in (
        ("requirements", requirements),
        ("artifacts", artifacts),
        ("topology", topology),
        ("usage", usage),
    ):
        app.include_router(module.router, prefix=f"/api/{name}")
    app.dependency_overrides[requirements.get_requirements] = lambda: store
    app.dependency_overrides[topology.get_topology_repository] = lambda: graph
    app.dependency_overrides[artifacts.get_artifacts] = lambda: FileSystemArtifactStore(
        tmp_path / "artifacts"
    )
    app.dependency_overrides[usage.get_usage_reader] = lambda: SQLUsageReader(engine)
    monkeypatch.setattr(requirements, "connected_names", lambda user: ["acme/app"])
    client = TestClient(app)

    def headers(workspace="one"):
        token = jwt.encode(
            {
                "sub": "1",
                "scope": {"workspace_id": workspace},
                "exp": time.time() + 300,
            },
            "dashboard-backend-test-secret",
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    return client, headers, store, engine


def test_requirements_priority_links_and_workspace_isolation(api):
    client, headers, store, _ = api
    item = {
        "id": "latency",
        "kind": "requirement",
        "status": "open",
        "summary": "Respond within one second",
        "properties": {"priority": "must"},
    }
    assert (
        client.put("/api/requirements", json=item, headers=headers()).status_code == 200
    )
    result = client.get("/api/requirements?priorities=must", headers=headers()).json()
    assert result["data"][0]["priority"] == "must"
    assert (
        client.get("/api/requirements?priorities=should", headers=headers()).json()[
            "data"
        ]
        == []
    )
    assert client.get("/api/requirements", headers=headers("two")).json()["data"] == []
    assert (
        client.get("/api/requirements?repo=outside/repo", headers=headers()).json()[
            "data"
        ]
        == []
    )
    link = {
        "id": "code",
        "requirement_id": "latency",
        "target_kind": "code",
        "target_id": "app.py",
    }
    assert (
        client.put("/api/requirements/links", json=link, headers=headers()).status_code
        == 200
    )
    coverage = client.get("/api/requirements/coverage", headers=headers()).json()[
        "data"
    ]["items"][0]
    assert (coverage["code_links"], coverage["test_links"]) == (1, 0)
    client.delete("/api/requirements/links/code", headers=headers())
    assert client.get("/api/requirements/links", headers=headers()).json()["data"] == []
    client.delete("/api/requirements/latency", headers=headers())
    assert client.get("/api/requirements", headers=headers()).json()["data"] == []


def test_artifact_roundtrip_limits_and_scoping(api, monkeypatch):
    client, headers, _, _ = api
    monkeypatch.setenv("ARTIFACT_MAX_UPLOAD_BYTES", "12")
    body = b"requirement"
    uploaded = client.post(
        "/api/artifacts/requirements/spec",
        content=body,
        headers={**headers(), "Content-Type": "text/plain"},
    )
    assert uploaded.status_code == 200
    data = uploaded.json()["data"]
    path = "/api/artifacts/requirements/spec/" + data["key"]["version"]
    assert data["digest"]["value"] == hashlib.sha256(body).hexdigest()
    assert client.get(path, headers=headers()).content == body
    assert client.get(path, headers=headers("two")).status_code == 404
    assert client.get(path).status_code in (401, 422)
    assert (
        client.post(
            "/api/artifacts/requirements/large",
            content=b"x" * 13,
            headers={**headers(), "Content-Type": "text/plain"},
        ).status_code
        == 413
    )
    assert (
        len(client.get("/api/artifacts/requirements", headers=headers()).json()["data"])
        == 1
    )


def test_word_document_roundtrip_and_unsupported_type(api):
    client, headers, _, _ = api
    body = (Path(__file__).parent / "fixtures/documents/test.docx").read_bytes()
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    response = client.post(
        "/api/artifacts/requirements/word",
        content=body,
        headers={**headers(), "Content-Type": media_type},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    downloaded = client.get(
        "/api/artifacts/requirements/word/" + data["key"]["version"], headers=headers()
    )
    assert downloaded.content == body
    assert downloaded.headers["content-type"] == media_type
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in downloaded.headers["content-disposition"]
    rejected = client.post(
        "/api/artifacts/requirements/unknown",
        content=body,
        headers={**headers(), "Content-Type": "application/octet-stream"},
    )
    assert rejected.status_code == 415
    assert (
        len(client.get("/api/artifacts/requirements", headers=headers()).json()["data"])
        == 1
    )


def test_atomic_topology_batch_rollback_and_retry(api):
    client, headers, _, _ = api
    payload = {
        "operation_id": "create",
        "entities": [{"id": "parent", "kind": "system", "status": "approved"}],
        "relationships": [
            {
                "id": "bad",
                "source_id": "parent",
                "target_id": "missing",
                "type": "contains",
                "status": "approved",
            }
        ],
    }
    assert (
        client.post("/api/topology/batch", json=payload, headers=headers()).status_code
        == 422
    )
    assert (
        client.post("/api/topology/search", json={}, headers=headers()).json()["data"][
            "entities"
        ]
        == []
    )
    payload["relationships"] = []
    for _ in range(2):
        assert (
            client.post(
                "/api/topology/batch", json=payload, headers=headers()
            ).status_code
            == 200
        )
    assert (
        len(
            client.post("/api/topology/search", json={}, headers=headers()).json()[
                "data"
            ]["entities"]
        )
        == 1
    )
    payload["entities"][0]["id"] = "different"
    assert (
        client.post("/api/topology/batch", json=payload, headers=headers()).status_code
        == 422
    )


def test_usage_exact_totals_subject_filter_and_workspace(api):
    client, headers, _, engine = api
    from src.core.usage import TokenUsage
    from src.core.usage.sql import SQLUsageRecorder
    from unittest.mock import patch

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        recorder = SQLUsageRecorder()
        recorder.record(
            TokenUsage(
                "provider",
                "model",
                10,
                2,
                cost_micro=7,
                purpose="initialization",
                owner_type="workspace",
                owner_id="one",
                subject_type="repository",
                subject_id="acme/app",
            )
        )
        recorder.record(
            TokenUsage(
                "provider",
                "model",
                20,
                3,
                cost_micro=9,
                purpose="requirement-assist",
                owner_type="workspace",
                owner_id="two",
                subject_type="repository",
                subject_id="acme/app",
            )
        )
    report = client.get("/api/usage", headers=headers()).json()["data"]
    assert report["total"]["cost_micro"] == 7
    assert report["by_purpose"][0]["name"] == "initialization"
    assert (
        client.get("/api/usage?repository=other/app", headers=headers()).json()["data"][
            "total"
        ]["calls"]
        == 0
    )
    assert client.get("/api/usage?period=1d", headers=headers()).status_code == 422


@pytest.mark.asyncio
async def test_http_writes_are_visible_over_mcp_and_review_selection(api):
    from mcp.shared.memory import create_connected_server_and_client_session
    from src.core.context import DefaultContextProvider
    from src.core.mcp import create_mcp_server
    from src.core.change_context import (
        ChangeSet,
        ChangedFile,
        DefaultChangeContextResolver,
    )
    from src.core.requirements import LinkedRequirementSelector

    client, headers, store, _ = api
    item = {
        "id": "reviewed",
        "kind": "requirement",
        "status": "open",
        "summary": "Keep the observable behavior",
        "properties": {"priority": "must"},
    }
    client.put("/api/requirements", json=item, headers=headers()).raise_for_status()
    client.put(
        "/api/requirements/links",
        json={
            "id": "trace",
            "requirement_id": "reviewed",
            "target_kind": "code",
            "target_id": "app.py",
        },
        headers=headers(),
    ).raise_for_status()
    server = create_mcp_server(
        DefaultContextProvider(requirements=store), requirements=store
    )
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "search_requirements", {"scope": {"workspace": "one"}}
        )
    assert result.structuredContent["items"][0]["properties"]["priority"] == "must"
    resolver = DefaultChangeContextResolver(
        requirements=LinkedRequirementSelector(store)
    )
    selected = resolver.resolve(
        ChangeSet(
            Scope.from_mapping({"repository": "acme/app"}),
            (ChangedFile("app.py"),),
            requirement_scopes=(Scope.from_mapping({"workspace": "one"}),),
        )
    )
    assert selected.requirements[0].id == "reviewed"


def test_suggestions_disclose_name_only_evidence():
    from src.core.topology.suggestions import suggest_groups

    groups = suggest_groups(["acme/shop-api", "acme/shop-ui"], ())
    assert groups[0]["sources"] == ["names"]
    assert groups[0]["unread_repositories"] == ["acme/shop-api", "acme/shop-ui"]
    assert groups[0]["confidence"] == 0.2


def test_inference_and_suggestions_preserve_manifest_evidence(api, monkeypatch):
    from pathlib import Path
    from src.core.topology.inference import parse_manifest

    client, headers, _, _ = api
    monkeypatch.setattr(
        requirements,
        "connected_names",
        lambda user: ["sourceant/sourceant", "sourceant/memory"],
    )
    fixtures = Path(__file__).parent / "unit" / "data" / "manifests"

    async def manifests(assets, token):
        result = []
        for asset in assets:
            memory = asset["repository"] == "sourceant/memory"
            result.append(
                parse_manifest(
                    asset["entity_id"],
                    asset["repository"],
                    "pyproject.toml" if memory else "requirements.txt",
                    (
                        fixtures
                        / (
                            "memory.pyproject.toml"
                            if memory
                            else "sourceant.requirements.txt"
                        )
                    ).read_text(),
                )
            )
        return tuple(result)

    monkeypatch.setattr(topology, "read_manifests", manifests)
    token = jwt.encode(
        {
            "sub": "1",
            "scope": {"workspace_id": "one"},
            "github_token": "test-only-placeholder",
            "exp": time.time() + 300,
        },
        "dashboard-backend-test-secret",
        algorithm="HS256",
    )
    auth = {"Authorization": "Bearer " + token}
    assets = [
        {"entity_id": "core", "repository": "sourceant/sourceant"},
        {"entity_id": "memory", "repository": "sourceant/memory"},
    ]
    for asset in assets:
        client.put(
            "/api/topology/entities",
            json={
                "id": asset["entity_id"],
                "kind": "repository",
                "status": "approved",
                "properties": {"name": asset["repository"], "system_id": "system"},
            },
            headers=auth,
        ).raise_for_status()
    response = client.post("/api/topology/infer", json={"assets": assets}, headers=auth)
    assert response.status_code == 200
    proposals = response.json()["data"]["proposed"]
    assert proposals and proposals[0]["status"] == "pending"
    assert proposals[0]["evidence"][0]["source"] == "sourceant/memory/pyproject.toml"
    suggested = client.post(
        "/api/topology/suggest",
        json={"repositories": [asset["repository"] for asset in assets]},
        headers=auth,
    ).json()["data"]["groups"]
    assert len(suggested) == 1
    assert suggested[0]["sources"] == ["manifest"]
    assert suggested[0]["evidence"]
