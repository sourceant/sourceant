from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine
from src.core.review.partition_store import SQLReviewWork


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


def test_json_migration_round_trip_preserves_progress_and_saved_responses(
    tmp_path, monkeypatch
):
    import json
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.migrations.versions import review_chunks
    from src.core.review.partitioning import partition

    fixtures = Path(__file__).parents[1] / "fixtures"
    captured = json.loads((fixtures / "deepseek/review.json").read_text())
    split = partition(
        (fixtures / "review-overview/full.diff").read_text(),
        cost=len,
        input_budget=2000,
    )
    chunk = split.chunks[0]
    targets = [
        {"path": file.file_path, "ranges": file.hunk_ranges} for file in chunk.files
    ]
    plan = {"parts": [{"id": chunk.id, "targets": targets}]}
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.sqlite"))
    store = SQLReviewWork(engine, create_schema=True)
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan=plan,
        maximum_calls=8,
    )
    store.reserve_call("run")
    store.save_response("run", "request", captured)
    lease = store.claim("run", chunk.id)
    assert store.finish("run", chunk.id, lease, {"review": captured, "coverage": []})
    before = store.chunks("run")
    with engine.begin() as connection:
        monkeypatch.setattr(
            review_chunks, "op", Operations(MigrationContext.configure(connection))
        )
        review_chunks.downgrade()
        review_chunks.upgrade()
    reopened = SQLReviewWork(engine)
    reopened.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan=plan,
        maximum_calls=8,
    )
    assert reopened.chunks("run") == before
    assert reopened.progress("run")["model_calls"] == 1
    assert reopened.response("run", "request") == captured
    assert reopened.answers("run")[chunk.id]["review"] == captured
