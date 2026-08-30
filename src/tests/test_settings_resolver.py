from src.core.settings import resolver


class TestStatelessSettings:
    """A deployment with no database has nothing stored, so nothing is read."""

    def test_a_setting_falls_back_without_reading_the_database(self, monkeypatch):
        reads = []

        def record(*args, **kwargs):
            reads.append(args)
            return None

        monkeypatch.setattr(resolver, "STATELESS_MODE", True)
        monkeypatch.setattr("src.models.config.Config.get_value", record)

        answer = resolver.resolve("model.name", repository="sourceant/sourceant")

        assert reads == []
        assert answer.source == "default"
        assert answer.value == ""

    def test_a_stored_value_still_wins_when_there_is_a_database(self, monkeypatch):
        monkeypatch.setattr(resolver, "STATELESS_MODE", False)
        monkeypatch.setattr(
            "src.models.config.Config.get_value",
            lambda *args, **kwargs: "anthropic/claude-sonnet-4-5",
        )

        answer = resolver.resolve("model.name", repository="sourceant/sourceant")

        assert answer.value == "anthropic/claude-sonnet-4-5"
        assert answer.source == "repository"
