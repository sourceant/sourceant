import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from src.api.routes import requirements
from src.auth import get_current_user
from src.core.requirements import SQLRequirementsRepository

SOURCE = """Feature: Workspace invitations
  Background:
    Given I own a workspace

  @id:invite
  Scenario Outline: Invite a teammate
    When I invite "<email>"
    Then they receive an invitation

    Examples:
      | email             |
      | person@example.org |
"""


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'requirements.db'}")
    store = SQLRequirementsRepository(engine, create_schema=True)
    app = FastAPI()
    app.include_router(requirements.router, prefix="/requirements")
    user = {"scope": {"workspace_id": "one"}}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[requirements.get_requirements] = lambda: store
    monkeypatch.setattr(requirements, "connected_names", lambda user: ["acme/app"])
    monkeypatch.setattr(requirements, "grouping", lambda: None)
    with TestClient(app) as client:
        yield client, user
    engine.dispose()


def write(client, source=SOURCE):
    return client.put(
        "/requirements",
        json={
            "id": "invitations",
            "type": "requirement",
            "status": "open",
            "summary": "Owners can invite teammates",
            "properties": {
                "rationale": "Work together",
                "behavior": {"source": source},
            },
        },
    )


def test_scenarios_round_trip_without_test_links(api):
    client, _ = api
    preview = client.post("/requirements/scenarios/parse", json={"source": SOURCE})
    assert preview.status_code == 200, preview.text
    parsed = preview.json()["data"]
    assert parsed["scenarios"][0]["id"] == "invite"
    assert parsed["scenarios"][0]["background"][0]["text"] == "I own a workspace"
    assert len(parsed["scenarios"][0]["examples"][0]["tableBody"]) == 1
    saved = write(client)
    assert saved.status_code == 200, saved.text
    read = client.get("/requirements").json()["data"][0]
    assert read["properties"]["behavior"] == parsed
    assert read["properties"]["rationale"] == "Work together"
    assert client.get("/requirements/links").json()["data"] == []


def test_scenario_links_are_scoped_and_track_the_linked_revision(api):
    client, user = api
    behavior = write(client).json()["data"]["properties"]["behavior"]
    payload = {
        "id": "invite-test",
        "requirement_id": "invitations",
        "target_type": "test",
        "target_id": "tests/invite.py",
        "properties": {"scenario_id": "invite"},
    }
    assert client.put("/requirements/links", json=payload).status_code == 200
    linked = client.get("/requirements/links").json()["data"][0]
    revision = behavior["scenarios"][0]["revision"]
    assert linked["properties"]["scenario_revision"] == revision
    user["scope"]["workspace_id"] = "two"
    assert client.get("/requirements").json()["data"] == []
    assert client.put("/requirements/links", json=payload).status_code == 422
    user["scope"]["workspace_id"] = "one"
    changed = write(
        client,
        SOURCE.replace("receive an invitation", "receive a time-limited invitation"),
    )
    assert changed.status_code == 200
    assert (
        changed.json()["data"]["properties"]["behavior"]["scenarios"][0]["revision"]
        != revision
    )
    assert client.get("/requirements/links").json()["data"][0] == linked
    assert client.put("/requirements/links", json=linked).status_code == 422
    payload["properties"]["scenario_id"] = "missing"
    assert client.put("/requirements/links", json=payload).status_code == 422


@pytest.mark.parametrize(
    "source", ["Given no feature", "Feature: Empty", SOURCE + SOURCE, "x" * 100_001]
)
def test_invalid_scenarios_cannot_be_saved(api, source):
    client, _ = api
    assert (
        client.post(
            "/requirements/scenarios/parse", json={"source": source}
        ).status_code
        == 422
    )
    assert write(client, source).status_code == 422
    assert client.get("/requirements").json()["data"] == []


def test_scenario_revision_ignores_line_numbers_but_includes_background(api):
    client, _ = api

    def parse(source):
        response = client.post("/requirements/scenarios/parse", json={"source": source})
        assert response.status_code == 200, response.text
        return response.json()["data"]["scenarios"][0]

    original = parse(SOURCE)
    assert parse("\n" + SOURCE)["revision"] == original["revision"]
    assert (
        parse(SOURCE.replace("own a workspace", "administer a workspace"))["revision"]
        != original["revision"]
    )


def test_cli_validates_a_feature_without_executing_it(tmp_path):
    from click.testing import CliRunner
    from src.cli.index_commands import requirements_group

    feature = tmp_path / "invitations.feature"
    feature.write_text(SOURCE)
    result = CliRunner().invoke(
        requirements_group, ["validate-scenarios", str(feature)]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["scenarios"][0]["id"] == "invite"


@pytest.mark.asyncio
async def test_scenarios_are_available_through_mcp(tmp_path):
    from mcp.shared.memory import create_connected_server_and_client_session
    from src.core.context import DefaultContextProvider
    from src.core.mcp import create_mcp_server

    engine = create_engine(f"sqlite:///{tmp_path / 'mcp.db'}")
    store = SQLRequirementsRepository(engine, create_schema=True)
    server = create_mcp_server(DefaultContextProvider(), requirements=store)
    async with create_connected_server_and_client_session(server) as session:
        parsed = await session.call_tool(
            "parse_requirement_scenarios", {"source": SOURCE}
        )
        assert not parsed.isError
        saved = await session.call_tool(
            "put_requirement",
            {
                "scope": {"workspace": "one"},
                "id": "invite",
                "summary": "Invite teammates",
                "properties": {"behavior": parsed.structuredContent},
            },
        )
        assert not saved.isError, saved
        assert saved.structuredContent["properties"]["behavior"]["source"] == SOURCE
        invalid = await session.call_tool(
            "link_requirement",
            {
                "scope": {"workspace": "one"},
                "id": "bad",
                "requirement_id": "invite",
                "target_type": "test",
                "target_id": "tests/invite.py",
                "properties": {"scenario_id": "missing"},
            },
        )
        assert invalid.isError
    engine.dispose()


def test_cli_import_and_export_preserve_the_document(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from src.cli import index_commands
    from src.core.knowledge import SQLKnowledgeRepository
    from src.core.requirements import Requirement
    from src.core.scope import Scope

    engine = create_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    store = SQLRequirementsRepository(engine, create_schema=True)
    SQLKnowledgeRepository(engine, create_schema=True)
    store.put(
        Scope.from_mapping({"repository": "acme/app"}),
        Requirement(
            id="invite", type="requirement", status="open", summary="Invite teammates"
        ),
    )
    monkeypatch.setattr(index_commands, "_engine", lambda: engine)
    feature = tmp_path / "invite.feature"
    feature.write_text(SOURCE)
    runner = CliRunner()
    imported = runner.invoke(
        index_commands.requirements_group,
        ["import-scenarios", "acme/app", "invite", str(feature)],
    )
    assert imported.exit_code == 0, imported.output
    exported = runner.invoke(
        index_commands.requirements_group, ["export-scenarios", "acme/app", "invite"]
    )
    assert exported.exit_code == 0, exported.output
    assert exported.output == SOURCE
    engine.dispose()


def test_removed_scenarios_do_not_erase_existing_evidence(api):
    client, _ = api
    write(client)
    assert (
        client.put(
            "/requirements/links",
            json={
                "id": "test",
                "requirement_id": "invitations",
                "target_type": "test",
                "target_id": "tests/invite.py",
                "properties": {"scenario_id": "invite"},
            },
        ).status_code
        == 200
    )
    saved = client.get("/requirements").json()["data"][0]
    saved["properties"]["behavior"] = None
    assert client.put("/requirements", json=saved).status_code == 200
    assert "behavior" not in client.get("/requirements").json()["data"][0]["properties"]
    assert len(client.get("/requirements/links").json()["data"]) == 1


def test_same_requirement_id_keeps_evidence_in_its_repository(api):
    client, _ = api
    workspace = write(client).json()["data"]
    repository = {**workspace, "repo": "acme/app"}
    assert client.put("/requirements", json=repository).status_code == 200
    link = {
        "id": "invite-test",
        "requirement_id": "invitations",
        "target_type": "test",
        "target_id": "tests/invite.py",
        "properties": {"scenario_id": "invite"},
        "repo": "acme/app",
    }
    assert client.put("/requirements/links", json=link).status_code == 200
    links = client.get("/requirements/links").json()["data"]
    assert len(links) == 1
    assert links[0]["repo"] == "acme/app"
    report = client.get("/requirements/coverage").json()["data"]["items"]
    assert {row["repo"] for row in report} == {"", "acme/app"}
    assert (
        client.put(
            "/requirements/links", json={**link, "repo": "foreign/app"}
        ).status_code
        == 403
    )
