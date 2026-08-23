import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import health


@pytest.fixture
def client():
    return TestClient(app)


class TestLiveness:
    def test_it_answers_without_touching_a_dependency(self, client, monkeypatch):
        def explode():
            raise AssertionError("liveness must not probe anything")

        monkeypatch.setitem(health.CHECKS, "database", explode)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["service"] == "sourceant-agent"


class TestReadiness:
    def test_it_reports_every_check_by_name(self, client):
        body = client.get("/health/ready").json()

        assert set(body["checks"]) == {"database", "queue", "graph", "plugins"}
        for check in body["checks"].values():
            assert check["status"] in {"ok", "failed", "skipped"}
            assert isinstance(check["duration_ms"], int)

    def test_an_unreachable_dependency_makes_it_unavailable(self, client, monkeypatch):
        def unreachable():
            raise ConnectionError("Error 111 connecting to redis:6379")

        monkeypatch.setitem(health.CHECKS, "queue", unreachable)

        response = client.get("/health/ready")

        assert response.status_code == 503
        assert response.json()["status"] == "failed"
        assert response.json()["checks"]["queue"]["status"] == "failed"

    def test_a_failure_does_not_hide_the_checks_that_passed(self, client, monkeypatch):
        monkeypatch.setitem(
            health.CHECKS, "graph", lambda: (_ for _ in ()).throw(ValueError("down"))
        )

        checks = client.get("/health/ready").json()["checks"]

        assert checks["graph"]["status"] == "failed"
        assert checks["plugins"]["status"] != "failed"

    def test_liveness_still_answers_while_readiness_is_failing(
        self, client, monkeypatch
    ):
        monkeypatch.setitem(
            health.CHECKS, "database", lambda: (_ for _ in ()).throw(OSError("gone"))
        )

        assert client.get("/health/ready").status_code == 503
        assert client.get("/health").status_code == 200

    def test_a_check_that_never_answers_is_a_failure_not_a_hang(
        self, client, monkeypatch
    ):
        import threading

        release = threading.Event()
        monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setitem(
            health.CHECKS, "graph", lambda: (release.wait(30), ("ok", None))[1]
        )

        try:
            body = client.get("/health/ready").json()
            assert body["checks"]["graph"]["status"] == "failed"
            assert "did not answer" in body["checks"]["graph"]["detail"]
        finally:
            release.set()


class TestIndex:
    def test_it_names_the_service_rather_than_asserting_a_status(self, client):
        body = client.get("/").json()

        assert body["service"] == "sourceant-agent"
        assert body["health"] == "/health"
        assert "status" not in body


class TestRepositoryEvents:
    def test_it_refuses_an_unauthenticated_caller(self, client):
        assert client.get("/repository-events").status_code == 422
