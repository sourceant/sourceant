from pathlib import Path

import pytest
from sqlalchemy import create_engine

from src.api.main import app
from src.api.routes import code as code_routes
from src.api.routes.code import get_code_index
from src.cli.local_index import add_repository
from src.core.code_index import InMemoryCodeIndex, SQLCodeIndexRepository
from src.core.code_index.indexer import RepositoryIndexer
from src.core.code_index.interfaces import CodeIndexReader
from src.core.services import service_registry
from src.tests.base_test import BaseTestCase


@pytest.fixture
def empty_registry():
    """Isolate the global registry so provider tests do not depend on plugin load order."""
    saved = dict(service_registry._registrations)
    service_registry._registrations.clear()
    yield service_registry
    service_registry._registrations.clear()
    service_registry._registrations.update(saved)


def test_code_index_prefers_a_registered_provider(empty_registry):
    provided = InMemoryCodeIndex()
    empty_registry.register(CodeIndexReader, provided, "test")

    assert get_code_index() is provided


def test_code_index_falls_back_when_no_plugin_provides_one(empty_registry, monkeypatch):
    monkeypatch.setattr(code_routes, "_fallback", None)
    monkeypatch.setattr(code_routes, "get_engine", lambda: None)

    resolved = get_code_index()

    assert isinstance(resolved, InMemoryCodeIndex)
    assert get_code_index() is resolved


class TestCodeApi(BaseTestCase):
    """Drives /api/code the way a local client does: real HTTP, no token."""

    @pytest.fixture(autouse=True)
    def indexed_repository(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "home"))
        source = tmp_path / "billing"
        (source / "app").mkdir(parents=True)
        (source / "tests").mkdir()
        (source / "app" / "charge.py").write_text(
            "import decimal\n\n\ndef charge(amount):\n    return amount\n",
            encoding="utf-8",
        )
        (source / "tests" / "test_charge.py").write_text(
            "def test_charge():\n    assert True\n", encoding="utf-8"
        )

        self.entry = add_repository(source, name="acme/billing")
        store = SQLCodeIndexRepository(
            create_engine(f"sqlite:///{tmp_path / 'code.db'}"), create_schema=True
        )
        RepositoryIndexer(store).index(self.entry.scope, Path(source))
        app.dependency_overrides[get_code_index] = lambda: store
        yield
        app.dependency_overrides.pop(get_code_index, None)

    def test_it_lists_the_repositories_registered_on_this_machine(self):
        response = self.client.get("/api/code/repositories")

        assert response.status_code == 200
        assert response.json()["data"] == [
            {"name": "acme/billing", "path": self.entry.path}
        ]

    def test_it_draws_a_registered_repository(self):
        response = self.client.get(
            "/api/code/graph", params={"repository": "acme/billing"}
        )

        assert response.status_code == 200
        body = response.json()["data"]
        assert body["truncated"] is False
        by_id = {node["id"]: node for node in body["nodes"]}
        assert by_id["file:app/charge.py"]["name"] == "charge.py"
        assert by_id["file:app/charge.py"]["kind"] == "file"
        assert by_id["file:app/charge.py"]["language"] == "python"
        assert by_id["file:app/charge.py"]["path"] == "app/charge.py"
        assert {link["type"] for link in body["links"]} == {"imports", "defines"}

    def test_a_file_is_a_file_whatever_language_it_is_written_in(self):
        """A drawing colouring Python files apart from Go ones colours the wrong
        question, so the language is its own field rather than the kind."""
        body = self.client.get(
            "/api/code/graph", params={"repository": "acme/billing"}
        ).json()["data"]

        by_name = {node["name"]: node for node in body["nodes"]}
        assert by_name["charge.py"]["kind"] == "file"
        assert by_name["charge.py"]["language"] == "python"
        assert by_name["charge.py"]["labels"] == ["File"]
        assert by_name["charge"]["kind"] == "function"
        assert "language" not in by_name["charge"]

    def test_a_drawing_is_told_how_busy_each_node_is(self):
        body = self.client.get(
            "/api/code/graph", params={"repository": "acme/billing"}
        ).json()["data"]

        by_name = {node["name"]: node for node in body["nodes"]}
        assert by_name["charge.py"]["degree"] >= 2
        assert all("community" in node for node in body["nodes"])

        # Everything here is one file's worth of code, so it is one part, and it
        # is named for where it lives rather than for a number.
        assert [part["name"] for part in body["communities"]] == ["app"]
        assert body["communities"][0]["size"] == len(body["nodes"])

    def test_it_leaves_tests_out_of_a_drawing_unless_they_are_asked_for(self):
        without = self.client.get(
            "/api/code/graph", params={"repository": "acme/billing"}
        ).json()["data"]
        with_tests = self.client.get(
            "/api/code/graph",
            params={"repository": "acme/billing", "include_tests": True},
        ).json()["data"]

        drawn = {node["path"] for node in without["nodes"]}
        assert "tests/test_charge.py" not in drawn
        assert "tests/test_charge.py" in {node["path"] for node in with_tests["nodes"]}

    def test_it_pages_the_nodes_in_one_file(self):
        response = self.client.get(
            "/api/code/nodes",
            params={"repository": "acme/billing", "file_path": "app/charge.py"},
        )

        assert response.status_code == 200
        body = response.json()["data"]
        assert body["has_more"] is False
        assert {node["path"] for node in body["nodes"]} == {"app/charge.py"}

    def test_it_serves_no_scope_that_was_not_registered_here(self):
        response = self.client.get(
            "/api/code/graph", params={"repository": "acme/payments"}
        )

        assert response.status_code == 404
        assert "not registered" in response.json()["detail"]
