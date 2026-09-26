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

    def test_tuning_is_marked_apart_from_what_a_person_chooses(self):
        body = self.client.get("/api/local/settings").json()

        assert self.of(body, "model.name")["advanced"] is False
        assert self.of(body, "model.api_key")["advanced"] is False
        assert self.of(body, "model.token_limit")["advanced"] is True
        assert self.of(body, "review.reading_budget")["advanced"] is True

    def test_no_two_settings_are_called_the_same_thing(self):
        body = self.client.get("/api/local/settings").json()

        labels = [item["label"] for item in body["data"]]
        assert len(labels) == len(set(labels))

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

    def test_the_models_that_can_be_named_here_are_offered(self):
        response = self.client.get("/api/local/settings/models")

        assert response.status_code == 200
        catalogue = response.json()["data"]
        assert catalogue, "nothing was offered, so a screen has nothing to show"
        assert {"provider", "models"} <= set(catalogue[0])

    def test_a_check_with_nothing_named_says_so_rather_than_asking(self):
        response = self.client.post("/api/local/settings/models/check", json={})

        assert response.status_code == 400

    def test_a_key_already_saved_is_the_one_checked(self, monkeypatch):
        asked = {}

        def _refused(model, api_key, base_url="", **_):
            asked.update(model=model, api_key=api_key)
            return None

        monkeypatch.setattr("src.core.model.catalogue.refused", _refused)
        self.client.put(
            "/api/local/settings/model.name", json={"value": "anthropic/one"}
        )
        self.client.put("/api/local/settings/model.api_key", json={"value": "sk-here"})

        response = self.client.post("/api/local/settings/models/check", json={})

        assert response.json()["data"] == {"usable": True, "reason": None}
        assert asked == {"model": "anthropic/one", "api_key": "sk-here"}

    def test_a_key_that_cannot_use_the_model_says_why(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.model.catalogue.refused",
            lambda *args, **kwargs: "That key cannot use that model.",
        )

        response = self.client.post(
            "/api/local/settings/models/check",
            json={"model": "anthropic/one", "api_key": "sk-wrong"},
        )

        assert response.json()["data"] == {
            "usable": False,
            "reason": "That key cannot use that model.",
        }

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
