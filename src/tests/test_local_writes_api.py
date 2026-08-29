import pytest
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes.code import get_code_index
from src.api.routes.knowledge import get_knowledge
from src.cli.local_index import add_repository, list_repositories
from src.core.code_index import SQLCodeIndexRepository
from src.core.knowledge import SQLKnowledgeRepository
from src.tests.base_test import BaseTestCase


class TestLocalWrites(BaseTestCase):
    """Drives /api/code and /api/knowledge the way the local UI does."""

    @pytest.fixture(autouse=True)
    def machine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", True)
        self.source = tmp_path / "billing"
        (self.source / "app").mkdir(parents=True)
        (self.source / "app" / "charge.py").write_text(
            "import decimal\n\n\ndef charge(amount):\n    return amount\n",
            encoding="utf-8",
        )
        engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
        app.dependency_overrides[get_code_index] = lambda: SQLCodeIndexRepository(
            engine, create_schema=True
        )
        app.dependency_overrides[get_knowledge] = lambda: SQLKnowledgeRepository(
            engine, create_schema=True
        )
        yield
        app.dependency_overrides.clear()

    def register(self, name="acme/billing"):
        return self.client.post(
            "/api/code/repositories",
            json={"path": str(self.source), "name": name},
        )

    def test_a_directory_can_be_covered_and_then_read(self):
        registered = self.register()
        listed = self.client.get("/api/code/repositories")

        assert registered.status_code == 200
        assert registered.json()["data"]["name"] == "acme/billing"
        assert [item["name"] for item in listed.json()["data"]] == ["acme/billing"]

    def test_a_directory_that_is_not_there_is_refused(self):
        response = self.client.post(
            "/api/code/repositories", json={"path": str(self.source / "nowhere")}
        )

        assert response.status_code == 400

    def test_indexing_reads_the_repository_into_the_graph(self):
        self.register()

        indexed = self.client.post(
            "/api/code/index", json={"repository": "acme/billing"}
        )
        graph = self.client.get(
            "/api/code/graph", params={"repository": "acme/billing"}
        )

        assert indexed.status_code == 200
        assert indexed.json()["data"][0]["indexed"] == 1
        assert any(
            node["path"] == "app/charge.py" for node in graph.json()["data"]["nodes"]
        )

    def test_dropping_a_repository_stops_it_being_answered_for(self):
        self.register()

        dropped = self.client.delete(
            "/api/code/repositories", params={"path": str(self.source)}
        )
        after = self.client.get(
            "/api/code/graph", params={"repository": "acme/billing"}
        )

        assert dropped.status_code == 200
        assert after.status_code == 404
        assert list_repositories() == []

    def test_knowledge_is_recorded_and_read_back(self):
        self.register()

        written = self.client.put(
            "/api/knowledge",
            json={
                "repository": "acme/billing",
                "id": "retry-limit",
                "kind": "decision",
                "status": "accepted",
                "summary": "Charges retry three times, then stop.",
                "properties": {"why": "The provider rate limits after four."},
            },
        )
        read = self.client.get("/api/knowledge", params={"repository": "acme/billing"})

        assert written.status_code == 200
        body = read.json()["data"]
        assert body["total"] == 1
        assert body["items"][0]["summary"] == "Charges retry three times, then stop."
        assert body["items"][0]["properties"]["why"].startswith("The provider")

    def test_knowledge_can_be_forgotten(self):
        self.register()
        self.client.put(
            "/api/knowledge",
            json={
                "repository": "acme/billing",
                "id": "retry-limit",
                "kind": "decision",
                "summary": "Charges retry three times.",
            },
        )

        forgotten = self.client.delete(
            "/api/knowledge", params={"repository": "acme/billing", "id": "retry-limit"}
        )
        read = self.client.get("/api/knowledge", params={"repository": "acme/billing"})

        assert forgotten.status_code == 200
        assert read.json()["data"]["total"] == 0

    def test_knowledge_is_kept_apart_by_repository(self):
        self.register()
        other = self.source.parent / "payments"
        other.mkdir()
        add_repository(other, name="acme/payments")
        self.client.put(
            "/api/knowledge",
            json={
                "repository": "acme/billing",
                "id": "retry-limit",
                "kind": "decision",
                "summary": "Only billing knows this.",
            },
        )

        read = self.client.get("/api/knowledge", params={"repository": "acme/payments"})

        assert read.json()["data"]["total"] == 0

    def test_a_repository_that_states_things_can_have_them_recorded(self):
        """It is knowledge already; it was just nowhere a tool could reach."""
        (self.source / "docs" / "adr").mkdir(parents=True)
        (self.source / "docs" / "adr" / "0001-retry.md").write_text(
            "# Retry charges three times\n\n"
            "## Context\n\nThe provider rate limits after four.\n\n"
            "## Decision\n\nA failed charge is retried three times.\n",
            encoding="utf-8",
        )
        self.register()

        started = self.client.post(
            "/api/knowledge/initialize", json={"repository": "acme/billing"}
        )
        read = self.client.get("/api/knowledge", params={"repository": "acme/billing"})

        assert started.status_code == 200
        assert started.json()["data"]["recorded"] == 1
        item = read.json()["data"]["items"][0]
        assert item["summary"] == "A failed charge is retried three times."
        assert item["properties"]["source"] == "docs/adr/0001-retry.md"
        assert item["properties"]["why"] == "The provider rate limits after four."

    def test_nobody_has_agreed_to_what_was_only_read_off_a_file(self):
        (self.source / "CONTRIBUTING.md").write_text(
            "## Conventions\n\nEvery route answers in the standard envelope.\n",
            encoding="utf-8",
        )
        self.register()

        self.client.post(
            "/api/knowledge/initialize", json={"repository": "acme/billing"}
        )
        read = self.client.get("/api/knowledge", params={"repository": "acme/billing"})

        assert [item["status"] for item in read.json()["data"]["items"]] == ["proposed"]

    def test_asking_what_a_repository_states_records_nothing(self):
        (self.source / "CONTRIBUTING.md").write_text(
            "## Conventions\n\nEvery route answers in the standard envelope.\n",
            encoding="utf-8",
        )
        self.register()

        asked = self.client.post(
            "/api/knowledge/initialize",
            json={"repository": "acme/billing", "dry_run": True},
        )
        read = self.client.get("/api/knowledge", params={"repository": "acme/billing"})

        assert len(asked.json()["data"]["found"]) == 1
        assert asked.json()["data"]["recorded"] == 0
        assert read.json()["data"]["total"] == 0

    def test_knowledge_about_a_repository_nobody_registered_is_refused(self):
        response = self.client.get("/api/knowledge", params={"repository": "acme/nope"})

        assert response.status_code == 404


class TestWritesAreOffAwayFromTheLocalServer(BaseTestCase):
    """What a deployment gets: reading, and nothing that changes the machine."""

    @pytest.fixture(autouse=True)
    def hosted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", False)
        self.source = tmp_path / "billing"
        self.source.mkdir()
        yield

    def test_a_deployment_cannot_be_told_to_cover_a_directory(self):
        response = self.client.post(
            "/api/code/repositories", json={"path": str(self.source)}
        )

        assert response.status_code == 403
        assert "sourceant serve" in response.json()["detail"]

    def test_a_deployment_cannot_be_told_to_index(self):
        assert self.client.post("/api/code/index", json={}).status_code == 403

    def test_a_deployment_cannot_be_told_to_drop_a_repository(self):
        response = self.client.delete(
            "/api/code/repositories", params={"path": str(self.source)}
        )

        assert response.status_code == 403

    def test_a_deployment_cannot_be_told_to_record_knowledge(self):
        response = self.client.put(
            "/api/knowledge",
            json={
                "repository": "acme/billing",
                "id": "x",
                "kind": "decision",
                "summary": "no",
            },
        )

        assert response.status_code == 403

    def test_reading_still_works_where_something_was_registered(self):
        add_repository(self.source, name="acme/billing")

        assert self.client.get("/api/code/repositories").status_code == 200
