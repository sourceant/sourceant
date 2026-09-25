import hashlib
import time
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.api.routes import artifacts, groups, requirements, topology, usage
from src.core.knowledge import InMemoryKnowledgeRepository
from src.core.groups import CheckedGroups, SQLGroupsRepository
from src.core.requirements import KnowledgeBackedRequirements, SQLRequirementsRepository
from src.core.requirements.grouping import GroupableRequirements
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
    filing = CheckedGroups(
        SQLGroupsRepository(engine, create_schema=True),
        (GroupableRequirements(store),),
    )
    TokenUsageRecord.__table__.create(engine, checkfirst=True)
    app = FastAPI()
    for name, module in (
        ("requirements", requirements),
        ("groups", groups),
        ("artifacts", artifacts),
        ("topology", topology),
        ("usage", usage),
    ):
        app.include_router(module.router, prefix=f"/api/{name}")
    app.dependency_overrides[requirements.get_requirements] = lambda: store
    app.dependency_overrides[groups.get_groups] = lambda: filing
    monkeypatch.setattr(requirements, "grouping", lambda: filing)
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


def test_groups_bucket_requirements_and_roll_up_what_they_hold(api):
    client, headers, _, _ = api
    for group in (
        {
            "id": "billing-v2",
            "name": "Billing v2",
            "type": "project",
            "status": "open",
        },
        {
            "id": "refunds",
            "name": "Refunds",
            "type": "feature",
            "status": "open",
            "parent_id": "billing-v2",
        },
    ):
        assert (
            client.put("/api/groups", json=group, headers=headers()).status_code == 200
        )

    for identity, repo in (("refund-speed", "acme/app"), ("refund-once", "")):
        assert (
            client.put(
                "/api/requirements",
                json={
                    "id": identity,
                    "kind": "requirement",
                    "status": "open",
                    "summary": "Refunds settle within one business day",
                    "repo": repo,
                },
                headers=headers(),
            ).status_code
            == 200
        )
    assert (
        client.put(
            "/api/requirements/links",
            json={
                "id": "code",
                "requirement_id": "refund-speed",
                "target_kind": "code",
                "target_id": "refund.py",
                "repo": "acme/app",
            },
            headers=headers(),
        ).status_code
        == 200
    )

    for identity, repo in (("refund-speed", "acme/app"), ("refund-once", "")):
        assert (
            client.put(
                f"/api/groups/refunds/members",
                json={
                    "member_type": "requirement",
                    "member_id": identity,
                    "repo": repo,
                },
                headers=headers(),
            ).status_code
            == 200
        )

    filed = client.get("/api/requirements?groups=refunds", headers=headers()).json()
    assert sorted(one["id"] for one in filed["data"]) == ["refund-once", "refund-speed"]
    assert {tuple(one["group_ids"]) for one in filed["data"]} == {("refunds",)}
    assert (
        client.get("/api/requirements?groups=disputes", headers=headers()).json()[
            "data"
        ]
        == []
    )

    outermost = client.get("/api/groups?roots=true", headers=headers()).json()
    assert [one["id"] for one in outermost["data"]] == ["billing-v2"]

    rolled = client.get("/api/groups/rollup?ids=billing-v2", headers=headers()).json()
    assert rolled["data"]["items"][0]["counts"]["requirement"] == {
        "total": 2,
        "covered": 1,
        "tested": 0,
    }
    assert rolled["data"]["items"][0]["descendants"] == 1


def test_a_requirement_every_project_answers_to_sits_in_each_of_them(api):
    """The case one group per requirement got wrong.

    A speed requirement is not part of one project any more than of the next, so
    each project counts it and taking it out of one leaves the others holding it.
    """
    client, headers, _, _ = api
    for identity, name in (("billing-v2", "Billing v2"), ("payments", "Payments")):
        client.put(
            "/api/groups",
            json={"id": identity, "name": name, "type": "project", "status": "open"},
            headers=headers(),
        )
    client.put(
        "/api/requirements",
        json={
            "id": "speed",
            "kind": "requirement",
            "status": "open",
            "summary": "Every service answers within a second",
        },
        headers=headers(),
    )
    for identity in ("billing-v2", "payments"):
        assert (
            client.put(
                f"/api/groups/{identity}/members",
                json={"member_type": "requirement", "member_id": "speed"},
                headers=headers(),
            ).status_code
            == 200
        )

    listed = client.get("/api/requirements", headers=headers()).json()["data"]
    assert sorted(one["group_ids"] for one in listed if one["id"] == "speed") == [
        ["billing-v2", "payments"]
    ]
    rolled = client.get(
        "/api/groups/rollup?ids=billing-v2&ids=payments", headers=headers()
    ).json()["data"]["items"]
    assert [one["counts"]["requirement"]["total"] for one in rolled] == [1, 1]

    client.delete("/api/groups/billing-v2/members/requirement/speed", headers=headers())
    left = client.get("/api/requirements?groups=payments", headers=headers()).json()
    assert [one["group_ids"] for one in left["data"]] == [["payments"]]


def test_asking_for_a_group_where_grouping_is_unavailable_says_so(api, monkeypatch):
    """An empty list would read as a group holding nothing."""
    client, headers, _, _ = api
    monkeypatch.setattr(requirements, "grouping", lambda: None)

    assert (
        client.get("/api/requirements?groups=refunds", headers=headers()).status_code
        == 503
    )
    assert client.get("/api/requirements", headers=headers()).status_code == 200


def test_a_group_stays_in_its_workspace_and_refuses_a_repository_outside_it(api):
    client, headers, _, _ = api
    assert (
        client.put(
            "/api/groups",
            json={
                "id": "refunds",
                "name": "Refunds",
                "type": "feature",
                "status": "open",
            },
            headers=headers(),
        ).status_code
        == 200
    )

    assert client.get("/api/groups", headers=headers()).json()["total"] == 1
    assert client.get("/api/groups", headers=headers("two")).json()["data"] == []
    assert (
        client.put(
            "/api/groups/refunds/members",
            json={
                "member_type": "requirement",
                "member_id": "anything",
                "repo": "outside/repo",
            },
            headers=headers(),
        ).status_code
        == 403
    )


def test_a_group_that_still_holds_something_is_not_deleted(api):
    client, headers, _, _ = api
    client.put(
        "/api/groups",
        json={"id": "refunds", "name": "Refunds", "type": "feature", "status": "open"},
        headers=headers(),
    )
    client.put(
        "/api/requirements",
        json={
            "id": "refund-speed",
            "kind": "requirement",
            "status": "open",
            "summary": "Refunds settle within one business day",
        },
        headers=headers(),
    )
    client.put(
        "/api/groups/refunds/members",
        json={"member_type": "requirement", "member_id": "refund-speed"},
        headers=headers(),
    )

    refused = client.delete("/api/groups/refunds", headers=headers())
    assert refused.status_code == 422
    assert "still holds things" in refused.json()["detail"]

    unfiled = client.delete(
        "/api/groups/refunds/members/requirement/refund-speed", headers=headers()
    )
    assert unfiled.json()["data"] == {"unfiled": True}
    assert client.delete("/api/groups/refunds", headers=headers()).status_code == 200
    assert client.get("/api/groups", headers=headers()).json()["data"] == []


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


def test_suggestions_name_the_system_to_join_and_what_has_none():
    """A read repository is already a system, and joining those is the point.

    Handing back names alone is what made the dashboard build a second, empty
    stand-in for a repository that already had a system with its code in it.
    """
    from src.core.topology.suggestions import suggest_groups

    groups = suggest_groups(
        ["acme/shop-api", "acme/shop-ui"],
        (),
        systems={"acme/shop-api": "system:abc"},
    )
    assert groups[0]["systems"] == [
        {"repository": "acme/shop-api", "system_id": "system:abc"}
    ]
    assert groups[0]["without_system"] == ["acme/shop-ui"]


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


def test_usage_reports_what_a_provider_cache_served(api):
    """A cache that is working has to be visible, or it cannot be tuned."""
    client, headers, _, engine = api
    from src.core.usage import TokenUsage
    from src.core.usage.sql import SQLUsageRecorder
    from unittest.mock import patch

    with patch("src.core.usage.sql.get_engine", return_value=engine):
        recorder = SQLUsageRecorder()
        recorder.record(
            TokenUsage(
                "anthropic",
                "claude-opus-5",
                1200,
                30,
                cached_input_tokens=900,
                cache_write_tokens=100,
                purpose="review",
                owner_type="workspace",
                owner_id="one",
                subject_type="repository",
                subject_id="acme/app",
            )
        )

    report = client.get("/api/usage", headers=headers()).json()["data"]
    assert report["total"]["input_tokens"] == 1200
    assert report["total"]["cached_input_tokens"] == 900
    assert report["total"]["cache_write_tokens"] == 100
    assert report["by_purpose"][0]["cached_input_tokens"] == 900
    assert report["by_organization"][0]["cached_input_tokens"] == 900
