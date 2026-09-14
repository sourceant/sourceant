"""A caller can ask whether a workspace holds one named repository."""

import os
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

import src.core.workspace as workspace_module
from src.api.main import app
from src.config.db import get_session

TEST_JWT_SECRET = "connected-by-name-secret"


def _headers(workspace: str) -> dict:
    token = jwt.encode(
        {
            "sub": "42",
            "scope": {"workspace_id": workspace, "repository_ids": []},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )

    return {"Authorization": f"Bearer {token}"}


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
        "/api/repos/connect", headers=_headers(workspace), json=_repository(full_name)
    )


def _connected(client, workspace: str, full_name: str | None = None):
    params = {"full_name": full_name} if full_name else {}

    return client.get(
        "/api/repos/connected", headers=_headers(workspace), params=params
    )


def _names(response) -> list[str]:
    return [item["full_name"] for item in response.json()["data"]["items"]]


def test_naming_a_repository_answers_about_that_one(client):
    _connect(client, "ws-a", "acme/web")
    _connect(client, "ws-a", "acme/api")

    assert _names(_connected(client, "ws-a", "acme/web")) == ["acme/web"]


def test_naming_none_answers_about_all_of_them(client):
    _connect(client, "ws-a", "acme/web")
    _connect(client, "ws-a", "acme/api")

    assert sorted(_names(_connected(client, "ws-a"))) == ["acme/api", "acme/web"]


def test_a_repository_the_workspace_does_not_hold_answers_empty(client):
    _connect(client, "ws-a", "acme/web")

    assert _names(_connected(client, "ws-a", "acme/api")) == []


def test_a_repository_another_workspace_holds_answers_empty(client):
    _connect(client, "ws-b", "acme/web")

    assert _names(_connected(client, "ws-a", "acme/web")) == []


def test_one_named_repository_is_one_page(client):
    _connect(client, "ws-a", "acme/web")
    _connect(client, "ws-a", "acme/api")

    answer = _connected(client, "ws-a", "acme/web").json()["data"]

    assert answer["total"] == 1
    assert answer["pages"] == 1
