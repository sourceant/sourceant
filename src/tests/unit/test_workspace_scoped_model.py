"""A model and its key belong to the workspace paying for them."""

import os
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from litellm.types.utils import Choices, Message, ModelResponse, Usage
from sqlmodel import SQLModel, Session, create_engine

from unittest.mock import patch

import src.core.workspace as workspace_module
from src.api.main import app
from src.config.db import get_session
from src.core.model import SettingsLLMSource

TEST_JWT_SECRET = "workspace-scoped-model-secret"


def _token(workspace: str) -> str:
    return jwt.encode(
        {
            "sub": "42",
            "scope": {"workspace_id": workspace, "repository_ids": []},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


def _headers(workspace: str) -> dict:
    return {"Authorization": f"Bearer {_token(workspace)}"}


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

    monkeypatch.setattr("src.models.config.get_session", sessions)
    monkeypatch.setattr(workspace_module, "get_engine", lambda: engine)
    monkeypatch.setattr(workspace_module, "STATELESS_MODE", False)
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as started:
        yield started
    app.dependency_overrides.clear()


def _connect(client, workspace: str, full_name: str):
    return client.post(
        "/api/repos/connect", headers=_headers(workspace), json=_repository(full_name)
    )


def _asked(provider, prompt="hello"):
    """What litellm was handed when this provider was asked something."""
    answer = ModelResponse(
        choices=[Choices(message=Message(content="ok", role="assistant"))],
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    with patch("litellm.completion", return_value=answer) as called:
        provider.generate_text(prompt)
    return called.call_args.kwargs


def _choose(client, workspace: str, key: str, value: str):
    return client.put(
        f"/api/settings/workspace/{workspace}/{key}",
        headers=_headers(workspace),
        json={"value": value},
    )


def test_a_workspace_key_is_the_key_its_repository_is_reviewed_with(client):
    assert _connect(client, "ws-a", "acme/web").status_code == 201
    assert _choose(client, "ws-a", "model.name", "moonshot/kimi-k2").status_code == 200
    assert _choose(client, "ws-a", "model.api_key", "ws-a-key").status_code == 200

    provider = SettingsLLMSource(fallback_model="").provider_for(repository="acme/web")
    asked = _asked(provider)

    assert asked["model"] == "moonshot/kimi-k2"
    assert asked["api_key"] == "ws-a-key"


def test_usage_for_that_call_is_owed_by_the_workspace(client):
    _connect(client, "ws-a", "acme/web")
    _choose(client, "ws-a", "model.name", "moonshot/kimi-k2")

    provider = SettingsLLMSource(fallback_model="").provider_for(repository="acme/web")
    with patch("src.core.usage.record_completion") as recorded:
        _asked(provider)

    assert recorded.call_args.kwargs["workspace"] == "ws-a"


def test_a_repository_two_workspaces_hold_resolves_to_neither_of_their_keys(client):
    _connect(client, "ws-a", "acme/web")
    _connect(client, "ws-b", "acme/web")
    _choose(client, "ws-a", "model.name", "moonshot/kimi-k2")
    _choose(client, "ws-a", "model.api_key", "ws-a-key")

    provider = SettingsLLMSource(fallback_model="").provider_for(repository="acme/web")

    assert provider is None


def test_a_workspace_cannot_write_a_key_into_another_workspace(client):
    _connect(client, "ws-a", "acme/web")

    response = client.put(
        "/api/settings/workspace/ws-a/model.api_key",
        headers=_headers("ws-b"),
        json={"value": "stolen"},
    )

    assert response.status_code == 403


def test_a_key_cannot_be_set_on_a_repository(client):
    _connect(client, "ws-a", "acme/web")

    response = client.put(
        "/api/settings/repository/acme%2Fweb/model.api_key",
        headers=_headers("ws-a"),
        json={"value": "per-repo"},
    )

    assert response.status_code == 422
