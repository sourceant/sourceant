import json

import pytest
from click.testing import CliRunner

from src.cli.index_commands import index_command
from src.cli.local_index import (
    RegistryError,
    add_repository,
    list_repositories,
    registry_path,
    remove_repository,
)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "state"))
    return tmp_path


def test_nothing_registered_is_an_empty_list(home):
    assert list_repositories() == []


def test_a_repository_survives_a_restart(home):
    (home / "billing").mkdir()

    add_repository(home / "billing", name="acme/billing")

    assert [item.name for item in list_repositories()] == ["acme/billing"]


def test_registering_the_same_path_twice_keeps_one(home):
    (home / "billing").mkdir()

    add_repository(home / "billing", name="acme/billing")
    add_repository(home / "billing", name="acme/renamed")

    assert [item.name for item in list_repositories()] == ["acme/renamed"]


def test_a_half_written_registry_is_not_silently_forgotten(home):
    (home / "billing").mkdir()
    add_repository(home / "billing", name="acme/billing")
    registry_path().write_text('[{"name": "acme/bil', encoding="utf-8")

    with pytest.raises(RegistryError):
        list_repositories()


def test_index_reports_an_unreadable_registry_without_a_traceback(home, monkeypatch):
    registry_path().write_text('[{"name": "acme/bil', encoding="utf-8")
    monkeypatch.setattr("src.cli.index_commands._store", lambda: object())

    result = CliRunner().invoke(index_command, ["--all"])

    assert result.exit_code == 1
    assert "could not be read" in result.output
    assert "Traceback" not in result.output


def test_a_failed_write_leaves_the_previous_list_intact(home, monkeypatch):
    (home / "billing").mkdir()
    (home / "shipping").mkdir()
    add_repository(home / "billing", name="acme/billing")
    before = registry_path().read_text(encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("src.cli.local_index.os.replace", _boom)
    with pytest.raises(OSError):
        add_repository(home / "shipping", name="acme/shipping")

    assert registry_path().read_text(encoding="utf-8") == before
    assert [item.name for item in list_repositories()] == ["acme/billing"]


def test_a_failed_write_leaves_no_stray_file(home, monkeypatch):
    (home / "billing").mkdir()
    add_repository(home / "billing", name="acme/billing")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("src.cli.local_index.os.replace", _boom)
    with pytest.raises(OSError):
        add_repository(home / "billing", name="acme/again")

    assert not list(registry_path().parent.glob("*.tmp"))


def test_removing_what_was_never_registered_says_so(home):
    assert remove_repository(home / "nowhere") is False


def test_the_registry_is_valid_json(home):
    (home / "billing").mkdir()

    add_repository(home / "billing", name="acme/billing")

    assert json.loads(registry_path().read_text(encoding="utf-8")) == [
        {"name": "acme/billing", "path": str((home / "billing").resolve())}
    ]
