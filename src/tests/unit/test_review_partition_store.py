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
