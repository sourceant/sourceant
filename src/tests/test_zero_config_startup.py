import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(autouse=True)
def nothing_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "state"))


def test_the_server_starts_with_nothing_configured():
    """What a supervising client relies on: no env, no secret, still serving."""
    with TestClient(app) as client:
        response = client.get("/api/code/repositories")

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_it_still_refuses_to_start_when_a_gateway_signs_its_tokens(monkeypatch):
    """A generated secret would reject every token the gateway sends, silently."""
    monkeypatch.setattr("src.auth.REQUIRE_GATEWAY", True)

    with pytest.raises(RuntimeError, match="REQUIRE_GATEWAY"):
        with TestClient(app):
            pass
