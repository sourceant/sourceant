"""A credential is written like any other setting and never read back."""

from src.api.routes.settings import _described
from src.core.settings.definitions import BY_KEY, Resolved


def described(key, value):
    return _described(
        Resolved(
            key=key, value=value, source="user", source_id="1", setting=BY_KEY[key]
        )
    )


class TestReadingBack:
    def test_a_key_never_comes_back(self):
        shown = described("model.api_key", "sk-a-real-looking-key")

        assert shown["value"] is None
        assert "sk-a-real-looking-key" not in str(shown)

    def test_a_screen_is_still_told_whether_one_is_set(self):
        assert described("model.api_key", "sk-something")["is_set"] is True
        assert described("model.api_key", "")["is_set"] is False

    def test_it_says_which_settings_are_credentials(self):
        assert described("model.api_key", "x")["secret"] is True
        assert described("model.name", "anthropic/claude-sonnet-4-5")["secret"] is False

    def test_everything_else_reads_back_as_it_was(self):
        shown = described("model.name", "anthropic/claude-sonnet-4-5")

        assert shown["value"] == "anthropic/claude-sonnet-4-5"
        assert shown["is_set"] is None


class TestWhatIsOffered:
    def test_a_model_can_be_named_and_reached_and_paid_for(self):
        assert {"model.name", "model.api_key", "model.base_url"} <= set(BY_KEY)

    def test_a_machine_can_set_them_without_an_organisation(self):
        """Nobody signs in to their own machine, so these are the user's own."""
        assert "user" in BY_KEY["model.api_key"].scopes
        assert "user" in BY_KEY["model.name"].scopes

    def test_none_of_it_is_on_by_default(self):
        """Reading a repository is deterministic and needs no model at all."""
        assert BY_KEY["model.name"].default == ""
        assert BY_KEY["model.api_key"].default == ""
