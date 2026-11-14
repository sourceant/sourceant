from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine
from src.core.review.work_store import SQLReviewWork


def test_finished_parts_survive_reopening_and_calls_are_shared(tmp_path):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "reviews.sqlite"),
        connect_args={"check_same_thread": False},
    )
    store = SQLReviewWork(engine, create_schema=True)
    plan = {"parts": [{"id": "first"}, {"id": "second"}]}
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan=plan,
        maximum_calls=3,
    )
    lease = store.claim("run", "first")
    assert lease
    assert store.claim("run", "first") is None
    assert store.finish("run", "first", lease, {"findings": []})
    reopened = SQLReviewWork(engine)
    assert reopened.answers("run") == {"first": {"findings": []}}
    assert reopened.claim("run", "first") is None

    def reserve(_):
        try:
            reopened.reserve_call("run")
            return True
        except RuntimeError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(reserve, range(8))) == 3
    reopened.cancel("run")
    assert reopened.claim("run", "second") is None


def test_cancelled_run_rejects_late_worker_result(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "cancel.sqlite"))
    store = SQLReviewWork(engine, create_schema=True)
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": [{"id": "first"}]},
        maximum_calls=3,
    )
    lease = store.claim("run", "first")
    store.cancel("run")
    assert not store.finish("run", "first", lease, {"findings": []})
    assert store.answers("run") == {}


def test_changed_plan_cannot_reset_a_persisted_allowance(tmp_path):
    import pytest

    store = SQLReviewWork(
        create_engine("sqlite:///" + str(tmp_path / "immutable.sqlite")),
        create_schema=True,
    )
    original = {"parts": [{"id": "first"}], "settings": "first"}
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan=original,
        maximum_calls=3,
    )
    store.reserve_call("run")
    with pytest.raises(ValueError, match="cannot change identity"):
        store.open(
            "run",
            workspace="ours",
            repository="owner/repo",
            revision="head",
            plan={**original, "settings": "second"},
            maximum_calls=256,
        )
    assert store.progress("run")["model_calls"] == 1
    assert store.progress("run")["maximum_model_calls"] == 3


def test_expired_worker_cannot_overwrite_a_replacement_result(tmp_path):
    store = SQLReviewWork(
        create_engine("sqlite:///" + str(tmp_path / "lease.sqlite")), create_schema=True
    )
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": [{"id": "first"}]},
        maximum_calls=3,
    )
    old = store.claim("run", "first", seconds=-1)
    current = store.claim("run", "first")
    assert current and current != old
    assert not store.finish("run", "first", old, {"findings": ["stale"]})
    assert store.finish("run", "first", current, {"findings": []})
    assert store.answers("run") == {"first": {"findings": []}}


def test_chunks_keep_scope_attempts_and_results_without_parent_recursion(tmp_path):
    store = SQLReviewWork(
        create_engine("sqlite:///" + str(tmp_path / "chunks.sqlite")),
        create_schema=True,
    )
    plan = {
        "parts": [
            {
                "id": "first",
                "targets": [{"path": "auth/session.py", "ranges": [[1, 2, 1, 3]]}],
            },
            {"id": "second", "group": "auth/session", "targets": []},
        ]
    }
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan=plan,
        maximum_calls=8,
    )
    chunks = {chunk.id: chunk for chunk in store.chunks("run")}
    assert chunks["first"].review_id == "run"
    assert chunks["first"].group == "auth"
    assert chunks["first"].files[0].ranges == ((1, 2, 1, 3),)
    assert chunks["first"].status == "pending"
    assert chunks["first"].attempts == 0
    assert not store.complete("run")
    first = store.claim("run", "first")
    assert store.claim("run", "first") is None
    assert {chunk.id: chunk for chunk in store.chunks("run")}["first"].attempts == 1
    store.release("run", "first", first)
    retry = store.claim("run", "first")
    assert store.finish("run", "first", retry, {"review": None, "coverage": []})
    assert not store.complete("run")
    second = store.claim("run", "second")
    assert second
    assert store.finish("run", "second", second, {"review": None, "coverage": []})
    assert store.complete("run")
    chunks = {chunk.id: chunk for chunk in store.chunks("run")}
    assert chunks["first"].status == "done"
    assert chunks["first"].attempts == 2
    assert chunks["second"].group == "auth/session"
    assert chunks["first"].result == {"review": None, "coverage": []}


def test_chunk_migration_creates_the_final_schema_and_supports_the_store(
    tmp_path, monkeypatch
):
    import json
    from pathlib import Path
    from sqlalchemy import inspect, JSON
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.migrations.versions import review_chunks
    from src.core.review.planning import build_chunks
    from src.core.review.work_store import metadata
    from src.core.execution_attempts import metadata as execution_metadata

    fixtures = Path(__file__).parents[1] / "fixtures"
    captured = json.loads((fixtures / "deepseek/review.json").read_text())
    chunks = build_chunks(
        (fixtures / "review-overview/full.diff").read_text(),
        review_id="run",
        cost=len,
        input_budget=2000,
    )
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.sqlite"))
    with engine.begin() as connection:
        monkeypatch.setattr(
            review_chunks, "op", Operations(MigrationContext.configure(connection))
        )
        review_chunks.upgrade()
    schema = inspect(engine)
    tables = {**metadata.tables, **execution_metadata.tables}
    assert set(schema.get_table_names()) == set(tables)
    assert "lease" not in {
        column["name"] for column in schema.get_columns("review_chunks")
    }
    assert "leased_until" not in {
        column["name"] for column in schema.get_columns("review_chunks")
    }
    for table in tables.values():
        columns = {column["name"]: column for column in schema.get_columns(table.name)}
        assert set(columns) == set(table.c.keys())
        for column in table.c:
            assert columns[column.name]["nullable"] == column.nullable
            if isinstance(column.type, JSON):
                assert isinstance(columns[column.name]["type"], JSON)
        assert schema.get_pk_constraint(table.name)["constrained_columns"] == list(
            table.primary_key.columns.keys()
        )
    store = SQLReviewWork(engine)
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={},
        chunks=chunks,
        maximum_calls=8,
    )
    store.reserve_call("run")
    store.save_response("run", "request", captured)
    chunk = chunks[0]
    lease = store.claim("run", chunk.id)
    assert store.finish("run", chunk.id, lease, {"review": captured, "coverage": []})
    assert store.progress("run")["model_calls"] == 1
    assert store.response("run", "request") == captured
    assert store.answer("run", chunk.id)["review"] == captured
    saved = {chunk.id: chunk for chunk in store.chunks("run")}[chunk.id]
    assert saved.diff == chunk.diff
    assert saved.files == chunk.files
    with engine.begin() as connection:
        monkeypatch.setattr(
            review_chunks, "op", Operations(MigrationContext.configure(connection))
        )
        review_chunks.downgrade()
        assert inspect(connection).get_table_names() == []
        review_chunks.upgrade()
    assert set(inspect(engine).get_table_names()) == set(tables)


def test_cancelled_review_cannot_replay_a_completed_chunk(tmp_path):
    import pytest

    store = SQLReviewWork(
        create_engine("sqlite:///" + str(tmp_path / "cancel-replay.sqlite")),
        create_schema=True,
    )
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": [{"id": "first"}]},
        maximum_calls=3,
    )
    lease = store.claim("run", "first")
    assert store.finish("run", "first", lease, {"review": None, "coverage": []})
    assert store.answer("run", "first") == {"review": None, "coverage": []}
    store.cancel("run")
    with pytest.raises(RuntimeError, match="no longer active"):
        store.answer("run", "first")


def test_execution_attempt_history_keeps_failures_and_retries_separate(tmp_path):
    from src.core.execution_attempts import ExecutionAttempt

    store = SQLReviewWork(
        create_engine("sqlite:///" + str(tmp_path / "history.sqlite")),
        create_schema=True,
    )
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": [{"id": "chunk"}]},
        maximum_calls=8,
    )
    first = store.claim("run", "chunk")
    assert isinstance(first, ExecutionAttempt)
    assert store.release("run", "chunk", first, error="ValueError")
    second = store.claim("run", "chunk")
    assert second.attempt == 2
    assert not store.finish("run", "chunk", first, {"findings": ["stale"]})
    assert store.finish("run", "chunk", second, {"findings": []})
    history = store.attempt_history("run", "chunk")
    assert [
        (attempt.attempt, attempt.status, attempt.error) for attempt in history
    ] == [(1, "failed", "ValueError"), (2, "succeeded", "")]
    assert all(
        attempt.finished_at is not None and attempt.finished_at >= attempt.started_at
        for attempt in history
    )
    assert store.chunks("run")[0].attempts == 2


def test_expired_execution_cannot_publish_without_a_replacement(tmp_path):
    store = SQLReviewWork(
        create_engine("sqlite:///" + str(tmp_path / "expired.sqlite")),
        create_schema=True,
    )
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": [{"id": "chunk"}]},
        maximum_calls=8,
    )
    expired = store.claim("run", "chunk", seconds=-1)
    assert not store.finish("run", "chunk", expired, {"findings": ["late"]})
    current = store.claim("run", "chunk")
    assert current and current.attempt == 2
    assert store.attempt_history("run", "chunk")[0].status == "expired"
    store.cancel("run")
    assert not store.finish("run", "chunk", current, {"findings": []})
    assert [attempt.status for attempt in store.attempt_history("run", "chunk")] == [
        "expired",
        "cancelled",
    ]
    assert store.chunks("run")[0].status == "failed"


def test_concurrent_claims_create_only_one_execution_attempt(tmp_path):
    store = SQLReviewWork(
        create_engine(
            "sqlite:///" + str(tmp_path / "claims.sqlite"),
            connect_args={"check_same_thread": False},
        ),
        create_schema=True,
    )
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": [{"id": "chunk"}]},
        maximum_calls=8,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim("run", "chunk"), range(8)))
    assert sum(attempt is not None for attempt in claims) == 1
    assert len(store.attempt_history("run", "chunk")) == 1
    assert store.chunks("run")[0].attempts == 1
