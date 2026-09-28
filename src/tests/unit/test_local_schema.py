import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from src.config import settings
from src.core.local_schema import ensure


def _url(path):
    return f"sqlite:///{path}"


@pytest.fixture
def index(tmp_path, monkeypatch):
    """A database of this machine's own, since the migration environment reads
    the url from the settings rather than from whatever a caller passes."""
    path = tmp_path / "sourceant.db"
    monkeypatch.setattr(settings, "DATABASE_URL", _url(path))
    return path


def test_an_empty_index_is_migrated(tmp_path, index):
    """The local command is the whole product on one machine.

    Nothing stands between the install and the first request, so serving an
    index nobody migrated is how a fresh install used to fail.
    """
    ensure(create_engine(_url(index)), _url(index))

    tables = set(inspect(create_engine(_url(index))).get_table_names())
    assert "alembic_version" in tables
    assert {"scopes", "object_groups", "requirements"} <= tables


def test_an_index_with_no_history_is_rebuilt_and_kept(tmp_path, index):
    """What a version that served without migrating left behind.

    A few tables the runtime made for itself and no history, which no migration
    can be applied to. The index is read back out of the repositories it came
    from, so it is rebuilt, and the old one is kept rather than dropped.
    """
    made = sqlite3.connect(index)
    made.execute("create table scopes (id integer primary key, qualifiers text)")
    made.commit()
    made.close()

    ensure(create_engine(_url(index)), _url(index))

    tables = set(inspect(create_engine(_url(index))).get_table_names())
    assert "alembic_version" in tables
    assert [one for one in tmp_path.iterdir() if one.name.endswith(".unmigrated")]


def test_an_index_already_at_head_is_left_alone(tmp_path, index):
    ensure(create_engine(_url(index)), _url(index))

    ensure(create_engine(_url(index)), _url(index))

    assert not [one for one in tmp_path.iterdir() if one.name.endswith(".unmigrated")]


def test_an_index_that_is_not_a_file_is_left_for_the_caller_to_migrate(tmp_path, index):
    """A deployment migrates as its own step, and its database is not a file to
    set aside. Nothing is moved; whatever the upgrade says stands."""
    made = sqlite3.connect(index)
    made.execute("create table scopes (id integer primary key)")
    made.commit()
    made.close()

    with pytest.raises(Exception):
        ensure(create_engine(_url(index)), "postgresql://nobody@nowhere/sourceant")

    assert not [one for one in tmp_path.iterdir() if one.name.endswith(".unmigrated")]
