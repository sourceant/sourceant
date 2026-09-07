"""Whose job it is to make the queue's tables."""

import sqlalchemy as sa

from src.core.jobs import _default
from src.core.jobs.models import INTERACTIVE, JobRequest
from src.core.jobs.sql import SQLJobStore


def test_the_store_makes_its_tables_when_asked_to(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path}/asked.db")

    store = SQLJobStore(engine, create_schema=True)

    assert sa.inspect(engine).has_table("jobs")
    assert store.enqueue(JobRequest(lane=INTERACTIVE, kind="test.work"))


def test_the_store_the_core_uses_leaves_them_to_the_migrations(tmp_path, monkeypatch):
    engine = sa.create_engine(f"sqlite:///{tmp_path}/migrated.db")
    monkeypatch.setattr("src.core.jobs.get_engine", lambda: engine)
    monkeypatch.setattr("src.core.jobs._core", None)

    _default()

    assert not sa.inspect(engine).has_table("jobs")
