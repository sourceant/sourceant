import pytest

from src.tests.base_test import BaseTestCase


class TestLocalSettings(BaseTestCase):
    """Nobody signs in to their own machine, so these answer without a token."""

    @pytest.fixture(autouse=True)
    def machine(self, monkeypatch):
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", True)
        yield
        self.client.delete("/api/local/settings/model.api_key")
        self.client.delete("/api/local/settings/model.name")

    def of(self, body, key):
        return next(item for item in body["data"] if item["key"] == key)

    def test_everything_configurable_here_is_offered(self):
        response = self.client.get("/api/local/settings")

        assert response.status_code == 200
        keys = {item["key"] for item in response.json()["data"]}
        assert {"model.name", "model.api_key", "model.base_url"} <= keys

    def test_a_model_can_be_chosen_and_read_back(self):
        written = self.client.put(
            "/api/local/settings/model.name",
            json={"value": "anthropic/claude-sonnet-4-5"},
        )
        read = self.client.get("/api/local/settings")

        assert written.status_code == 200
        assert self.of(read.json(), "model.name")["value"] == (
            "anthropic/claude-sonnet-4-5"
        )

    def test_a_key_is_written_and_never_read_back(self):
        self.client.put(
            "/api/local/settings/model.api_key", json={"value": "sk-a-real-looking-key"}
        )

        response = self.client.get("/api/local/settings")

        assert "sk-a-real-looking-key" not in response.text
        shown = self.of(response.json(), "model.api_key")
        assert shown["value"] is None
        assert shown["is_set"] is True

    def test_a_screen_can_tell_a_key_has_never_been_set(self):
        response = self.client.get("/api/local/settings")

        assert self.of(response.json(), "model.api_key")["is_set"] is False

    def test_a_setting_can_be_put_back_to_what_it_was(self):
        self.client.put(
            "/api/local/settings/model.name", json={"value": "openai/gpt-4o"}
        )

        self.client.delete("/api/local/settings/model.name")
        read = self.client.get("/api/local/settings")

        assert self.of(read.json(), "model.name")["value"] == ""

    def test_a_setting_nobody_declared_is_refused(self):
        response = self.client.put(
            "/api/local/settings/model.favourite_colour", json={"value": "blue"}
        )

        assert response.status_code == 404


class TestAwayFromTheLocalServer(BaseTestCase):
    """A deployment has signed-in users, and answers about them, not about it."""

    @pytest.fixture(autouse=True)
    def hosted(self, monkeypatch):
        monkeypatch.setattr("src.api.routes.code.LOCAL_MODE", False)

    def test_nothing_here_answers(self):
        assert self.client.get("/api/local/settings").status_code == 403

    def test_and_nothing_here_can_be_set(self):
        response = self.client.put(
            "/api/local/settings/model.api_key", json={"value": "sk-no"}
        )

        assert response.status_code == 403
