"""What may be named as a model, and whether a key may actually use it."""

import os
import time
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api.main import app

TEST_JWT_SECRET = "model-catalogue-test-secret"


def _headers() -> dict:
    token = jwt.encode(
        {
            "sub": "42",
            "scope": {"workspace_id": "7", "repository_ids": []},
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    with TestClient(app) as started:
        yield started


def test_the_models_offered_come_from_the_router_rather_than_a_list_here(client):
    """A list written by hand goes stale. This one ships with the thing that
    does the routing, so it describes what can actually be reached."""
    answered = client.get("/api/settings/models", headers=_headers())

    assert answered.status_code == 200
    catalogue = answered.json()["data"]
    providers = {entry["provider"] for entry in catalogue}
    assert {"anthropic", "openai", "moonshot"} <= providers
    for entry in catalogue:
        assert entry["models"], f"{entry['provider']} offered nothing"


def test_a_model_the_key_cannot_use_is_reported_before_it_is_saved(client):
    """A provider's catalogue says what exists. An account may use a subset, and
    the two only differ once somebody is already waiting on a scan."""
    with patch(
        "litellm.completion",
        side_effect=Exception(
            "MoonshotException - Not found the model kimi-k2-0905-preview"
        ),
    ):
        answered = client.post(
            "/api/settings/models/check",
            headers=_headers(),
            json={"model": "moonshot/kimi-k2-0905-preview", "api_key": "a-key"},
        )

    assert answered.status_code == 200
    said = answered.json()["data"]
    assert said["usable"] is False
    assert "Not found the model" in said["reason"]


def test_a_model_the_key_can_use_says_so(client):
    with patch("litellm.completion", return_value=object()):
        answered = client.post(
            "/api/settings/models/check",
            headers=_headers(),
            json={"model": "moonshot/kimi-k2.7-code", "api_key": "a-key"},
        )

    assert answered.status_code == 200
    assert answered.json()["data"] == {"usable": True, "reason": None}


def test_a_null_base_url_is_taken_as_none_given(client):
    """The gateway in front of this turns empty strings into nulls, so a model
    with no endpoint of its own arrives as null rather than absent."""
    with patch("litellm.completion", return_value=object()) as called:
        answered = client.post(
            "/api/settings/models/check",
            headers=_headers(),
            json={
                "model": "moonshot/kimi-k2.7-code",
                "api_key": "a-key",
                "base_url": None,
            },
        )

    assert answered.status_code == 200
    assert answered.json()["data"]["usable"] is True
    assert "api_base" not in called.call_args.kwargs
