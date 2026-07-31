from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.settings import router
from src.auth import get_current_user
from src.models.config import ConfigType


def test_user_can_write_and_read_own_setting(monkeypatch):
    entries = {}

    class FakeConfig:
        @staticmethod
        def get_value(scope, scope_id, key, default=None):
            return entries.get((scope, scope_id, key), default)

        @staticmethod
        def set_value(scope, scope_id, key, value, type=ConfigType.STRING):
            entries[(scope, scope_id, key)] = value

        @staticmethod
        def delete_value(scope, scope_id, key):
            return entries.pop((scope, scope_id, key), None) is not None

    monkeypatch.setattr("src.core.settings.resolver.Config", FakeConfig)
    app = FastAPI()
    app.include_router(router, prefix="/api/settings")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "42"}

    with TestClient(app) as client:
        written = client.put(
            "/api/settings/user/42/review.reuse_days",
            json={"value": 3},
        )
        read = client.get("/api/settings/user/42")

    assert written.status_code == 200
    assert written.json()["data"]["source"] == "user"
    assert read.status_code == 200
    assert read.json()["data"][0]["value"] == 3


def test_user_cannot_write_another_users_setting():
    app = FastAPI()
    app.include_router(router, prefix="/api/settings")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "42"}

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/user/84/review.reuse_days",
            json={"value": 3},
        )

    assert response.status_code == 403
