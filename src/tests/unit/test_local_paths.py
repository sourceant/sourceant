import stat
from pathlib import Path

from src.config.paths import (
    data_dir,
    default_database_url,
    ensure_data_dir,
    local_jwt_secret,
)


def test_sourceant_home_wins_over_the_xdg_location(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "chosen"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "ignored"))

    assert data_dir() == tmp_path / "chosen"


def test_the_xdg_location_is_used_when_no_home_is_set(monkeypatch, tmp_path):
    monkeypatch.delenv("SOURCEANT_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert data_dir() == tmp_path / "sourceant"


def test_it_falls_back_to_the_conventional_user_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("SOURCEANT_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert data_dir() == tmp_path / ".local" / "share" / "sourceant"


def test_the_directory_is_created_only_when_it_is_asked_for(monkeypatch, tmp_path):
    target = tmp_path / "made" / "on" / "demand"
    monkeypatch.setenv("SOURCEANT_HOME", str(target))

    assert not target.exists()
    assert ensure_data_dir() == target
    assert target.is_dir()


def test_a_developer_with_no_configuration_gets_a_sqlite_database(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "state"))

    url = default_database_url()

    assert url == f"sqlite:///{tmp_path / 'state' / 'sourceant.db'}"
    assert (tmp_path / "state").is_dir()


def test_a_developer_with_no_configuration_gets_a_signing_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "state"))

    secret = local_jwt_secret()

    assert secret
    assert (tmp_path / "state" / "jwt_secret").read_text(encoding="utf-8") == secret


def test_the_secret_is_kept_so_a_restart_does_not_invalidate_its_tokens(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "state"))

    assert local_jwt_secret() == local_jwt_secret()


def test_the_secret_is_readable_only_by_its_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCEANT_HOME", str(tmp_path / "state"))

    local_jwt_secret()

    mode = (tmp_path / "state" / "jwt_secret").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
