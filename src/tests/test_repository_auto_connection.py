import time
from concurrent.futures import ThreadPoolExecutor

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from src.api.routes import repos, routing
from src.auth import ROUTING_AUDIENCE
from src.config.db import get_session
from src.models.connected_repository import ConnectedRepository
from src.models.repository import Repository
import src.core.workspace as workspaces


@pytest.fixture
def api(tmp_path, monkeypatch):
    secret = "routing-tests-placeholder-not-a-secret"
    monkeypatch.setenv("JWT_SECRET", secret)
    engine = create_engine(f"sqlite:///{tmp_path / 'routing.db'}")
    SQLModel.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(repos.router, prefix="/api/repos")
    app.include_router(routing.router, prefix="/api/routing")

    def sessions():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    monkeypatch.setattr(workspaces, "get_engine", lambda: engine)
    monkeypatch.setattr(workspaces, "STATELESS_MODE", False)

    def headers(workspace=None, registration=True):
        claims = {"sub": "1", "exp": int(time.time()) + 300}
        if workspace:
            claims["scope"] = {"workspace_id": workspace}
            if registration:
                claims["scope"]["repository_registration"] = True
        else:
            claims["aud"] = ROUTING_AUDIENCE
        return {
            "Authorization": "Bearer " + jwt.encode(claims, secret, algorithm="HS256")
        }

    with TestClient(app) as client:
        yield client, headers, engine
    engine.dispose()


def connection(name="sourceant/core", automatic=True):
    owner, repo = name.split("/")
    return {
        "github_id": 1,
        "full_name": name,
        "name": repo,
        "owner": owner,
        "owner_type": "Organization",
        "url": f"https://github.com/{name}",
        "only_if_unconnected": automatic,
    }


def test_auto_connection_is_idempotent_and_does_not_share_existing_repositories(api):
    client, headers, engine = api
    request = connection()
    first = client.post("/api/repos/connect", headers=headers("one"), json=request)
    assert first.status_code == 201
    assert (
        client.post(
            "/api/repos/connect", headers=headers("one"), json=request
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/repos/connect", headers=headers("two"), json=request
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/repos/connect",
            headers=headers("two"),
            json=connection(automatic=False),
        ).status_code
        == 201
    )
    with Session(engine) as session:
        assert len(session.exec(select(Repository)).all()) == 1
        assert len(session.exec(select(ConnectedRepository)).all()) == 2


def test_simultaneous_auto_connections_choose_only_one_workspace(api):
    client, headers, engine = api

    def connect(workspace):
        return client.post(
            "/api/repos/connect", headers=headers(workspace), json=connection()
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(connect, ["one", "two"]))
    assert sorted(statuses) == [201, 409]
    with Session(engine) as session:
        assert len(session.exec(select(Repository)).all()) == 1
        assert len(session.exec(select(ConnectedRepository)).all()) == 1


def test_organization_lookup_is_case_insensitive_exact_and_gateway_only(api):
    client, headers, _ = api
    for name, workspace in [
        ("SourceAnt/core", "one"),
        ("sourceant/web", "one"),
        ("sourceant/docs", "two"),
        ("sourceant-other/api", "three"),
    ]:
        assert (
            client.post(
                "/api/repos/connect", headers=headers(workspace), json=connection(name)
            ).status_code
            == 201
        )
    result = client.get(
        "/api/routing/organization", params={"owner": "SOURCEANT"}, headers=headers()
    )
    assert result.status_code == 200
    assert sorted(result.json()["data"]["workspaces"]) == ["one", "two"]
    assert (
        client.get(
            "/api/routing/organization",
            params={"owner": "sourceant"},
            headers=headers("one"),
        ).status_code
        == 401
    )


def test_case_changes_cannot_bypass_existing_connection(api):
    client, headers, _ = api
    assert (
        client.post(
            "/api/repos/connect", headers=headers("one"), json=connection()
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/repos/connect",
            headers=headers("two"),
            json=connection("SourceAnt/Core"),
        ).status_code
        == 409
    )


def test_user_tokens_cannot_probe_other_workspace_connections(api):
    client, headers, _ = api
    for name in ["sourceant/core", "sourceant/unconnected"]:
        assert (
            client.post(
                "/api/repos/connect",
                headers=headers("two", registration=False),
                json=connection(name),
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/api/repos/connect", headers=headers("one"), json=connection()
        ).status_code
        == 201
    )
    for name in ["sourceant/core", "sourceant/unconnected"]:
        assert (
            client.post(
                "/api/repos/connect",
                headers=headers("two", registration=False),
                json=connection(name),
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/api/repos/connect",
            headers=headers("two", registration=False),
            json=connection(automatic=False),
        ).status_code
        == 201
    )
