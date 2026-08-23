import pytest

from src.core.settings import definitions
from src.core.settings.definitions import ORGANIZATION, REPOSITORY, USER, Setting
from src.core.settings.resolver import organization_of
from src.models.config import ConfigType


def test_structural_context_file_limit_is_repository_and_organization_scoped():
    setting = definitions.get("review.structural_context_file_limit")

    assert setting.default == 20
    assert setting.minimum == 1
    assert setting.maximum == 100
    assert setting.scopes == (REPOSITORY, ORGANIZATION)


@pytest.fixture
def store(monkeypatch):
    """A configuration store held in memory, so resolution is tested on its own."""
    entries: dict[tuple[str, str, str], object] = {}

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
    return entries


@pytest.fixture
def reuse_days(monkeypatch):
    setting = Setting(
        key="review.reuse_days",
        label="Reuse a review for",
        description="",
        type=ConfigType.INT,
        scopes=(USER, REPOSITORY, ORGANIZATION),
        default=7,
        minimum=0,
        maximum=90,
    )
    monkeypatch.setattr(definitions, "SETTINGS", (setting,))
    monkeypatch.setattr(definitions, "BY_KEY", {setting.key: setting})
    return setting


class TestResolution:
    def test_a_user_value_wins_for_that_user(self, store, reuse_days):
        from src.core.settings.resolver import resolve, set_value

        set_value(ORGANIZATION, "acme", "review.reuse_days", 14)
        set_value(REPOSITORY, "acme/web", "review.reuse_days", 2)
        set_value(USER, "42", "review.reuse_days", 1)

        answer = resolve(
            "review.reuse_days",
            user="42",
            repository="acme/web",
        )

        assert answer.value == 1
        assert answer.source == USER

    def test_one_user_does_not_answer_for_another(self, store, reuse_days):
        from src.core.settings.resolver import resolve, set_value

        set_value(USER, "42", "review.reuse_days", 1)

        assert resolve("review.reuse_days", user="84").value == 7

    def test_falls_back_to_the_shipped_default(self, store, reuse_days):
        from src.core.settings.resolver import resolve

        answer = resolve("review.reuse_days", repository="acme/web")

        assert answer.value == 7
        assert answer.source == "default"
        assert answer.source_id is None

    def test_a_repository_takes_what_its_organisation_says(self, store, reuse_days):
        from src.core.settings.resolver import resolve, set_value

        set_value(ORGANIZATION, "acme", "review.reuse_days", 14)
        answer = resolve("review.reuse_days", repository="acme/web")

        assert answer.value == 14
        assert answer.source == ORGANIZATION
        assert answer.source_id == "acme"

    def test_a_repository_may_depart_from_its_organisation(self, store, reuse_days):
        from src.core.settings.resolver import resolve, set_value

        set_value(ORGANIZATION, "acme", "review.reuse_days", 14)
        set_value(REPOSITORY, "acme/web", "review.reuse_days", 2)
        answer = resolve("review.reuse_days", repository="acme/web")

        assert answer.value == 2
        assert answer.source == REPOSITORY

    def test_clearing_a_repository_value_inherits_again(self, store, reuse_days):
        from src.core.settings.resolver import clear_value, resolve, set_value

        set_value(ORGANIZATION, "acme", "review.reuse_days", 14)
        set_value(REPOSITORY, "acme/web", "review.reuse_days", 2)
        clear_value(REPOSITORY, "acme/web", "review.reuse_days")

        assert resolve("review.reuse_days", repository="acme/web").value == 14

    def test_one_repository_does_not_answer_for_another(self, store, reuse_days):
        from src.core.settings.resolver import resolve, set_value

        set_value(REPOSITORY, "acme/web", "review.reuse_days", 2)

        assert resolve("review.reuse_days", repository="acme/api").value == 7

    def test_a_stored_value_that_is_no_longer_valid_is_ignored(self, store, reuse_days):
        from src.core.settings.resolver import resolve

        store[(REPOSITORY, "acme/web", "review.reuse_days")] = 5000

        answer = resolve("review.reuse_days", repository="acme/web")

        assert answer.value == 7
        assert answer.source == "default"


class TestWriting:
    def test_refuses_a_value_outside_what_the_setting_allows(self, store, reuse_days):
        from src.core.settings.resolver import set_value

        with pytest.raises(ValueError):
            set_value(REPOSITORY, "acme/web", "review.reuse_days", 500)

    def test_refuses_a_setting_it_does_not_know(self, store, reuse_days):
        from src.core.settings.resolver import set_value

        with pytest.raises(KeyError):
            set_value(REPOSITORY, "acme/web", "review.invented", 1)

    def test_reads_a_value_written_as_text(self, store, reuse_days):
        from src.core.settings.resolver import resolve, set_value

        set_value(REPOSITORY, "acme/web", "review.reuse_days", "3")

        assert resolve("review.reuse_days", repository="acme/web").value == 3


class TestOrganizationOf:
    def test_takes_the_owner_from_a_full_name(self):
        assert organization_of("sourceant/dashboard") == "sourceant"

    def test_has_no_organisation_without_one(self):
        assert organization_of("dashboard") is None
