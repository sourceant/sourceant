"""The gateway can ask which workspaces have connected a repository."""

import os
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import src.core.workspace as workspace_module
from src.api.main import app
from src.auth import ROUTING_AUDIENCE
from src.config.db import get_session

TEST_JWT_SECRET = "routing-lookup-secret"


def _person(workspace: str) -> str:
    return jwt.encode(
        {
            "sub": "42",
            "scope": {"workspace_id": workspace, "repository_ids": []},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


def _gateway() -> str:
    return jwt.encode(
        {
            "sub": "gateway",
            "aud": ROUTING_AUDIENCE,
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


def _repository(full_name: str) -> dict:
    owner, _, name = full_name.partition("/")
    return {
        "github_id": abs(hash(full_name)) % 10_000_000,
        "full_name": full_name,
        "name": name,
        "owner": owner,
        "url": f"https://github.com/{full_name}",
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def sessions():
        yield Session(engine)

    monkeypatch.setattr(workspace_module, "get_engine", lambda: engine)
    monkeypatch.setattr(workspace_module, "STATELESS_MODE", False)
    app.dependency_overrides[get_session] = sessions
    yield TestClient(app)
    app.dependency_overrides.clear()


def _connect(client, workspace: str, full_name: str):
    return client.post(
        "/api/repos/connect",
        headers={"Authorization": f"Bearer {_person(workspace)}"},
        json=_repository(full_name),
    )


def _disconnect(client, workspace: str, repo_id: int):
    return client.delete(
        f"/api/repos/{repo_id}/disconnect",
        headers={"Authorization": f"Bearer {_person(workspace)}"},
    )


def _ask(client, full_name: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(
        "/api/routing/repository", params={"full_name": full_name}, headers=headers
    )


def test_the_gateway_is_told_which_workspace_holds_a_repository(client):
    _connect(client, "ws-a", "acme/web")

    answer = _ask(client, "acme/web", _gateway())

    assert answer.status_code == 200
    assert answer.json()["data"]["workspaces"] == ["ws-a"]


def test_every_workspace_holding_it_is_named(client):
    _connect(client, "ws-a", "acme/web")
    _connect(client, "ws-b", "acme/web")

    answer = _ask(client, "acme/web", _gateway())

    assert sorted(answer.json()["data"]["workspaces"]) == ["ws-a", "ws-b"]


def test_a_repository_nobody_connected_names_no_workspace(client):
    answer = _ask(client, "acme/nothing", _gateway())

    assert answer.status_code == 200
    assert answer.json()["data"]["workspaces"] == []


def test_a_repository_disconnected_is_no_longer_named(client):
    repo_id = _connect(client, "ws-a", "acme/web").json()["data"]["id"]
    _disconnect(client, "ws-a", repo_id)

    assert _ask(client, "acme/web", _gateway()).json()["data"]["workspaces"] == []


def test_a_persons_token_cannot_ask_where_a_repository_belongs(client):
    _connect(client, "ws-a", "acme/web")

    assert _ask(client, "acme/web", _person("ws-a")).status_code == 401


def test_an_unsigned_caller_cannot_ask_where_a_repository_belongs(client):
    _connect(client, "ws-a", "acme/web")

    assert _ask(client, "acme/web", None).status_code == 422
