import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.api.routes import requirements
from src.auth import get_current_user
from src.core.requirements import SQLRequirementsRepository
from src.core.requirements.assurance_sql import SQLAssuranceRepository


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'assurance.db'}")
    store = SQLRequirementsRepository(engine, create_schema=True)
    assurance = SQLAssuranceRepository(engine, create_schema=True)
    app = FastAPI()
    app.include_router(requirements.router, prefix="/requirements")
    user = {"user_id": "reviewer-1", "scope": {"workspace_id": "one"}}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[requirements.get_requirements] = lambda: store
    app.dependency_overrides[requirements.get_assurance] = lambda: assurance
    monkeypatch.setattr(requirements, "connected_names", lambda user: ["acme/app"])
    monkeypatch.setattr(requirements, "grouping", lambda: None)
    with TestClient(app) as client:
        yield client, user, engine
    engine.dispose()


def requirement(client, identifier="search", summary="Search responds within 500 ms"):
    response = client.put(
        "/requirements",
        json={
            "id": identifier,
            "type": "requirement",
            "status": "open",
            "summary": summary,
        },
    )
    assert response.status_code == 200, response.text


def criterion(client, identifier="search", expected=""):
    response = client.put(
        "/requirements/criteria",
        json={
            "criterion": {
                "id": "latency",
                "requirement_id": identifier,
                "statement": "95% of requests complete within 500 ms at the agreed load",
                "method": "analysis",
            },
            "expected_revision": expected,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def evidence(client, identifier="benchmark-1"):
    response = client.post(
        "/requirements/evidence",
        json={
            "id": identifier,
            "type": "benchmark",
            "title": "Search latency measurement",
            "source_ref": "artifact:benchmark-report",
            "observed_at": "2026-09-28T10:00:00Z",
            "subject_revision": "build-42",
            "environment": {"concurrency": 100},
            "result": {"p95_ms": 450},
            "producer": "benchmark runner",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def decision(item, identifier="assessment-1", outcome="passed", supersedes=""):
    return {
        "id": identifier,
        "requirement_id": item["requirement_id"],
        "criterion_id": item["id"],
        "criterion_revision": item["revision"],
        "requirement_revision": item["requirement_revision"],
        "outcome": outcome,
        "rationale": "Observed p95 is 450 ms against the 500 ms threshold",
        "evidence_ids": ["benchmark-1"],
        "supersedes": supersedes,
    }


def report(client, identifier="search"):
    response = client.get(
        "/requirements/assurance", params={"requirement_id": identifier}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_evidence_is_reusable_and_references_never_establish_satisfaction(api):
    client, _, _ = api
    requirement(client)
    assert report(client)["state"] == "undefined"
    assert (
        client.put(
            "/requirements/links",
            json={
                "id": "test-ref",
                "requirement_id": "search",
                "target_type": "test",
                "target_id": "tests/search.py",
            },
        ).status_code
        == 200
    )
    item = criterion(client)
    assert report(client)["state"] == "unassessed"
    saved = evidence(client)
    assert saved["recorded_by"] == "reviewer-1"
    assert (
        client.post("/requirements/assessments", json=decision(item)).status_code == 200
    )
    assert report(client)["state"] == "passed"
    requirement(client, "other-search")
    other = criterion(client, "other-search")
    assert (
        client.post(
            "/requirements/assessments", json=decision(other, "assessment-2")
        ).status_code
        == 200
    )
    assert client.get("/requirements/evidence").json()["data"]["total"] == 1
    assert report(client, "other-search")["state"] == "passed"


def test_requirement_changes_invalidate_assessments_and_preserve_history(api):
    client, _, _ = api
    requirement(client)
    item = criterion(client)
    evidence(client)
    assert (
        client.post(
            "/requirements/assessments", json=decision(item, outcome="failed")
        ).status_code
        == 200
    )
    requirement(client, summary="Search responds within 300 ms")
    assert report(client)["state"] == "needs_review"
    assert (
        client.post(
            "/requirements/assessments",
            json=decision(item, "assessment-2", supersedes="assessment-1"),
        ).status_code
        == 422
    )
    revised = criterion(client, expected=item["revision"])
    assert (
        client.post(
            "/requirements/assessments",
            json=decision(revised, "assessment-2", "waived", "assessment-1"),
        ).status_code
        == 200
    )
    current = report(client)
    assert current["state"] == "waived"
    history = client.get(
        "/requirements/assurance/history", params={"requirement_id": "search"}
    ).json()["data"]
    assert [item["outcome"] for item in history["assessments"]["items"]] == [
        "waived",
        "failed",
    ]
    assert history["criteria"]["total"] == 2
    assert (
        client.post(
            "/requirements/assessments",
            json=decision(revised, "assessment-3", supersedes="assessment-1"),
        ).status_code
        == 422
    )


def test_evidence_is_immutable_and_scope_is_enforced(api):
    client, user, _ = api
    requirement(client)
    item = criterion(client)
    saved = evidence(client)
    assert client.post("/requirements/evidence", json=saved).status_code == 422
    user["scope"]["workspace_id"] = "two"
    requirement(client)
    other = criterion(client)
    assert client.get("/requirements/evidence").json()["data"]["items"] == []
    assert (
        client.post("/requirements/assessments", json=decision(other)).status_code
        == 422
    )
    user["scope"]["workspace_id"] = "one"
    assert (
        client.post(
            "/requirements/assessments", json={**decision(item), "evidence_ids": []}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/requirements/evidence", params={"repo": "foreign/repo"}
        ).status_code
        == 403
    )


def test_relationship_meaning_is_separate_from_target_type(api):
    client, _, _ = api
    requirement(client)
    response = client.put(
        "/requirements/links",
        json={
            "id": "need",
            "requirement_id": "search",
            "target_type": "stakeholder_need",
            "target_id": "support-search",
            "relation": "derived_from",
        },
    )
    assert response.status_code == 200, response.text
    saved = client.get("/requirements/links").json()["data"][0]
    assert saved["relation"] == "derived_from"
    assert saved["target_type"] == "stakeholder_need"
    assert report(client)["state"] == "undefined"


def test_history_is_paged_and_keeps_original_requirement_baselines(api, monkeypatch):
    client, _, engine = api
    requirement(client)
    first = criterion(client)
    evidence(client)
    assert (
        client.post("/requirements/assessments", json=decision(first)).status_code
        == 200
    )
    requirement(client, summary="Search responds within 300 ms")
    from datetime import datetime, timezone
    from src.core.requirements import assurance_sql

    class EarlierClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2020, 1, 1, tzinfo=timezone.utc)

    with monkeypatch.context() as patch:
        patch.setattr(assurance_sql, "datetime", EarlierClock)
        latest = criterion(client, expected=first["revision"])
    assert report(client)["criteria"][0]["revision"] == latest["revision"]
    history = client.get(
        "/requirements/assurance/history",
        params={"requirement_id": "search", "limit": 1},
    ).json()["data"]
    assert history["criteria"]["total"] == 2
    assert history["criteria"]["has_more"] is True
    assert len(history["criteria"]["items"]) == 1
    assert (
        history["baselines"][0]["requirement"]["summary"]
        == "Search responds within 500 ms"
    )
    assert client.get("/requirements/evidence/benchmark-1").json()["data"][
        "result"
    ] == {"p95_ms": 450}
    assert (
        client.get(
            "/requirements/assurance/history",
            params={"requirement_id": "search", "limit": 101},
        ).status_code
        == 422
    )


def test_new_migration_preserves_existing_requirements_and_can_be_reversed(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from src.core.requirements import Requirement, RequirementQuery
    from src.core.scope import Scope
    from src.migrations.versions import requirement_assurance

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    store = SQLRequirementsRepository(engine, create_schema=True)
    scope = Scope.from_mapping({"workspace": "one"})
    store.put(
        scope,
        Requirement("existing", "requirement", "open", "Preserve this requirement"),
    )
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            requirement_assurance.upgrade()
        assert inspect(connection).has_table("requirement_assessments")
    assurance = SQLAssuranceRepository(engine)
    assert assurance.report(scope, "existing")["state"] == "undefined"
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            requirement_assurance.downgrade()
        assert not inspect(connection).has_table("requirement_assessments")
    assert (
        store.search(RequirementQuery(scope=scope)).items[0].summary
        == "Preserve this requirement"
    )
    engine.dispose()


@pytest.mark.asyncio
async def test_assurance_mcp_uses_the_same_revision_and_evidence_rules(api):
    from mcp.shared.memory import create_connected_server_and_client_session
    from src.core.context import DefaultContextProvider
    from src.core.mcp import create_mcp_server

    client, _, engine = api
    requirement(client)
    item = criterion(client)
    evidence(client)
    server = create_mcp_server(
        DefaultContextProvider(),
        requirements=SQLRequirementsRepository(engine),
        assurance=SQLAssuranceRepository(engine),
    )
    async with create_connected_server_and_client_session(server) as session:
        scope = {"workspace": "one"}
        tools = {
            tool.name: tool.inputSchema for tool in (await session.list_tools()).tools
        }
        assert "type" in tools["put_requirement"]["properties"]
        assert "kind" not in tools["put_requirement"]["properties"]
        assert "types" in tools["search_requirements"]["properties"]
        assert "kinds" not in tools["search_requirements"]["properties"]
        assert "target_type" in tools["link_requirement"]["properties"]
        result = await session.call_tool(
            "put_requirement",
            {
                "scope": scope,
                "id": "private",
                "summary": "Keep data private",
                "type": "constraint",
            },
        )
        assert not result.isError, result.content
        assert result.structuredContent["type"] == "constraint"
        result = await session.call_tool(
            "search_requirements", {"scope": scope, "types": ["constraint"]}
        )
        assert [item["id"] for item in result.structuredContent["items"]] == ["private"]
        result = await session.call_tool(
            "assess_requirement_criterion",
            {"scope": scope, "assessment": decision(item)},
        )
        assert not result.isError, result.content
        assert result.structuredContent["recorded_by"] == "local"
        result = await session.call_tool(
            "get_requirement_assurance", {"scope": scope, "requirement_id": "search"}
        )
        assert result.structuredContent["state"] == "passed"
        result = await session.call_tool(
            "get_requirement_evidence", {"scope": scope, "evidence_id": "benchmark-1"}
        )
        assert result.structuredContent["result"] == {"p95_ms": 450}
        result = await session.call_tool(
            "get_requirement_assessment_history",
            {"scope": scope, "requirement_id": "search", "limit": 1},
        )
        assert result.structuredContent["assessments"]["total"] == 1
        result = await session.call_tool(
            "assess_requirement_criterion",
            {"scope": scope, "assessment": decision(item, "duplicate")},
        )
        assert result.isError


def test_nontext_relationship_and_empty_observations_are_rejected(api):
    client, _, _ = api
    requirement(client)
    assert (
        client.put(
            "/requirements/links",
            json={
                "id": "bad",
                "requirement_id": "search",
                "target_type": "test",
                "target_id": "tests/search.py",
                "properties": {"relation": {"invalid": True}},
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/requirements/evidence",
            json={
                "id": "empty",
                "type": "inspection",
                "title": "Nothing observed",
                "source_ref": "artifact:empty",
                "observed_at": "2026-09-28T10:00:00Z",
                "result": {},
            },
        ).status_code
        == 422
    )


def test_requirement_type_is_the_only_public_classification_field(api):
    client, _, _ = api
    payload = {
        "id": "typed",
        "type": "constraint",
        "status": "open",
        "summary": "Keep data private",
    }
    saved = client.put("/requirements", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["type"] == "constraint"
    assert "kind" not in saved.json()["data"]
    assert (
        client.get("/requirements", params={"types": "constraint"}).json()["data"][0][
            "id"
        ]
        == "typed"
    )
    assert (
        client.get("/requirements", params={"types": "requirement"}).json()["data"]
        == []
    )
    assert (
        client.put("/requirements", json={**payload, "kind": "constraint"}).status_code
        == 422
    )
    assert (
        client.put(
            "/requirements/links",
            json={
                "id": "old",
                "requirement_id": "typed",
                "target_kind": "code",
                "target_id": "src/main.py",
            },
        ).status_code
        == 422
    )
