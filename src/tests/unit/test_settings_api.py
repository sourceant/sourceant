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
