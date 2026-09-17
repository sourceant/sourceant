import os
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from src.api.main import app
from src.core.settings import USER, Setting
from src.core.settings import definitions
from src.models.config import ConfigType

TEST_JWT_SECRET = "settings-api-test-secret"


def _token(
    user_id: str = "42",
    repository_names: tuple[str, ...] = ("acme/web",),
) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "scope": {
                "workspace_id": "workspace-1",
                "repository_ids": [101],
                "repository_names": list(repository_names),
            },
            "exp": int(time.time()) + 300,
        },
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )


@pytest.fixture
def settings_client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    preference = Setting(
        key="test.preference",
        label="Test preference",
        description="",
        type=ConfigType.INT,
        scopes=(USER,),
        default=7,
    )
    settings = (*definitions.SETTINGS, preference)
    monkeypatch.setattr(definitions, "SETTINGS", settings)
    monkeypatch.setattr(
        definitions,
        "BY_KEY",
        {setting.key: setting for setting in settings},
    )

    def sessions():
        yield Session(engine)

    monkeypatch.setattr("src.models.config.get_session", sessions)
    with TestClient(app) as client:
        yield client


def _headers(**token_overrides) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(**token_overrides)}"}


def test_user_setting_round_trips_through_http_and_real_auth(settings_client):
    written = settings_client.put(
        "/api/settings/user/42/test.preference",
        headers=_headers(),
        json={"value": 3},
    )
    read = settings_client.get(
        "/api/settings/user/42",
        headers=_headers(),
    )
    deleted = settings_client.delete(
        "/api/settings/user/42/test.preference",
        headers=_headers(),
    )

    assert written.status_code == 200
    assert written.json()["data"]["source"] == "user"
    assert read.status_code == 200
    current = next(
        item for item in read.json()["data"] if item["key"] == "test.preference"
    )
    assert current["value"] == 3
    assert deleted.status_code == 200
    assert deleted.json()["data"]["source"] == "default"


@pytest.mark.parametrize("method", ("get", "put", "delete"))
def test_user_cannot_access_another_users_setting(settings_client, method):
    path = "/api/settings/user/84"
    arguments = {"headers": _headers()}
    if method != "get":
        path += "/test.preference"
    if method == "put":
        arguments["json"] = {"value": 3}

    response = getattr(settings_client, method)(path, **arguments)

    assert response.status_code == 403


@pytest.mark.parametrize("deleted_key", ("model.name", "model.api_key"))
def test_deleting_workspace_provider_restores_personal_model_and_key(
    settings_client, deleted_key
):
    for scope, identifier, values in (
        (
            "user",
            "42",
            {
                "model.name": "deepseek/deepseek-v4-flash",
                "model.api_key": "your-personal-api-key-here",
            },
        ),
        (
            "workspace",
            "workspace-1",
            {
                "model.name": "gemini/gemini-3.5-flash",
                "model.api_key": "your-workspace-api-key-here",
                "model.base_url": "https://example.com",
            },
        ),
    ):
        for key, value in values.items():
            assert (
                settings_client.put(
                    f"/api/settings/{scope}/{identifier}/{key}",
                    headers=_headers(),
                    json={"value": value},
                ).status_code
                == 200
            )

    deleted = settings_client.delete(
        f"/api/settings/workspace/workspace-1/{deleted_key}", headers=_headers()
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["source"] == "user"
    response = settings_client.get(
        "/api/settings/workspace/workspace-1", headers=_headers()
    )
    settings = {one["key"]: one for one in response.json()["data"]}
    assert settings["model.name"]["value"] == "deepseek/deepseek-v4-flash"
    assert settings["model.api_key"]["source_id"] == "42"
    assert settings["model.api_key"]["is_set"] is True
    assert settings["model.api_key"]["value"] is None
    assert settings["model.base_url"]["value"] == ""
    assert "your-personal-api-key-here" not in response.text
    assert "your-workspace-api-key-here" not in response.text


def test_workspace_fallback_does_not_use_another_users_key(settings_client):
    for key, value in {
        "model.name": "deepseek/deepseek-v4-flash",
        "model.api_key": "your-api-key-here",
    }.items():
        settings_client.put(
            f"/api/settings/user/42/{key}", headers=_headers(), json={"value": value}
        )
    response = settings_client.get(
        "/api/settings/workspace/workspace-1", headers=_headers(user_id="84")
    )
    settings = {one["key"]: one for one in response.json()["data"]}
    assert settings["model.api_key"]["is_set"] is False
    assert settings["model.name"]["value"] == ""


@pytest.mark.parametrize(
    "workspace_model", ("gemini/gemini-3.5-flash", "unknown-provider/model")
)
def test_workspace_model_never_inherits_a_different_providers_key(
    settings_client, workspace_model
):
    for scope, identifier, key, value in (
        ("user", "42", "model.name", "deepseek/deepseek-v4-flash"),
        ("user", "42", "model.api_key", "your-api-key-here"),
        ("workspace", "workspace-1", "model.name", workspace_model),
    ):
        assert (
            settings_client.put(
                f"/api/settings/{scope}/{identifier}/{key}",
                headers=_headers(),
                json={"value": value},
            ).status_code
            == 200
        )
    response = settings_client.get(
        "/api/settings/workspace/workspace-1", headers=_headers()
    )
    settings = {one["key"]: one for one in response.json()["data"]}
    assert settings["model.name"]["value"] == workspace_model
    assert settings["model.api_key"]["is_set"] is False


def test_orphaned_key_is_visible_but_never_returned(settings_client):
    settings_client.put(
        "/api/settings/workspace/workspace-1/model.api_key",
        headers=_headers(),
        json={"value": "your-orphaned-key-here"},
    )
    response = settings_client.get(
        "/api/settings/workspace/workspace-1", headers=_headers()
    )
    key = next(one for one in response.json()["data"] if one["key"] == "model.api_key")
    assert key["stored_here"] is True
    assert key["stored_is_set"] is True
    assert key["is_set"] is False
    assert key["value"] is None
    assert key["stored_value"] is None
    assert "your-orphaned-key-here" not in response.text


def test_inherited_key_keeps_its_endpoint_with_another_model_from_same_provider(
    settings_client,
):
    for scope, identifier, values in (
        (
            "user",
            "42",
            {
                "model.name": "openai/gpt-4o",
                "model.api_key": "your-api-key-here",
                "model.base_url": "https://personal.example.com",
            },
        ),
        (
            "workspace",
            "workspace-1",
            {
                "model.name": "openai/gpt-4o-mini",
                "model.base_url": "https://workspace.example.com",
            },
        ),
    ):
        for key, value in values.items():
            assert (
                settings_client.put(
                    f"/api/settings/{scope}/{identifier}/{key}",
                    headers=_headers(),
                    json={"value": value},
                ).status_code
                == 200
            )
    response = settings_client.get(
        "/api/settings/workspace/workspace-1", headers=_headers()
    )
    settings = {one["key"]: one for one in response.json()["data"]}
    assert settings["model.name"]["value"] == "openai/gpt-4o-mini"
    assert settings["model.api_key"]["source"] == "user"
    assert settings["model.api_key"]["is_set"] is True
    assert settings["model.base_url"]["value"] == "https://personal.example.com"


def test_invalid_repository_key_deletion_preserves_model(settings_client):
    path = "/api/settings/repository/acme%2Fweb"
    assert (
        settings_client.put(
            f"{path}/model.name",
            headers=_headers(),
            json={"value": "openai/gpt-4o"},
        ).status_code
        == 200
    )
    assert (
        settings_client.delete(f"{path}/model.api_key", headers=_headers()).status_code
        == 404
    )
    response = settings_client.get(path, headers=_headers())
    model = next(one for one in response.json()["data"] if one["key"] == "model.name")
    assert model["stored_value"] == "openai/gpt-4o"


def test_repository_provider_deletion_only_clears_repository_settings(settings_client):
    for scope, identifier, values in (
        (
            "user",
            "42",
            {"model.name": "openai/gpt-4o", "model.api_key": "your-api-key-here"},
        ),
        (
            "repository",
            "acme/web",
            {
                "model.name": "openai/gpt-4o-mini",
                "model.base_url": "https://example.com",
            },
        ),
    ):
        for key, value in values.items():
            assert (
                settings_client.put(
                    f"/api/settings/{scope}/{identifier}/{key}",
                    headers=_headers(),
                    json={"value": value},
                ).status_code
                == 200
            )
    deleted = settings_client.delete(
        "/api/settings/repository/acme/web/model.name", headers=_headers()
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["value"] == "openai/gpt-4o"
    response = settings_client.get(
        "/api/settings/repository/acme/web", headers=_headers()
    )
    settings = {one["key"]: one for one in response.json()["data"]}
    assert settings["model.name"]["stored_here"] is False
    assert settings["model.base_url"]["stored_here"] is False
    personal = settings_client.get("/api/settings/user/42", headers=_headers())
    key = next(one for one in personal.json()["data"] if one["key"] == "model.api_key")
    assert key["stored_is_set"] is True


def test_turning_reviews_off_is_offered_where_it_can_be_set(settings_client):
    """A switch nobody can reach from the screen is not a switch."""
    for scope in ("repository", "workspace"):
        offered = settings_client.get(
            f"/api/settings/catalogue?scope={scope}", headers=_headers()
        )

        assert offered.status_code == 200
        keys = {item["key"] for item in offered.json()["data"]}
        assert {"review.enabled", "review.draft_pull_requests"} <= keys


def test_reviews_are_on_until_a_repository_says_otherwise(settings_client):
    read = settings_client.get(
        "/api/settings/repository/acme%2Fweb", headers=_headers()
    )

    standing = next(
        item for item in read.json()["data"] if item["key"] == "review.enabled"
    )
    assert standing["value"] is True
    assert standing["source"] == "default"


def test_one_repository_turns_reviews_off_without_touching_the_rest(settings_client):
    written = settings_client.put(
        "/api/settings/repository/acme%2Fweb/review.enabled",
        headers=_headers(),
        json={"value": False},
    )
    read = settings_client.get(
        "/api/settings/repository/acme%2Fweb", headers=_headers()
    )

    assert written.status_code == 200
    standing = next(
        item for item in read.json()["data"] if item["key"] == "review.enabled"
    )
    assert standing["value"] is False
    assert standing["source"] == "repository"
