import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import litellm
import pytest
from sqlalchemy import create_engine

from src.core.review.partition_store import SQLReviewWork
from src.core.review.partitioning import PartitionCapacityError
from src.core.review_budget import ReviewContinuation
from src.core.review_performance import current_budget, review_session
from src.llms.litellm_provider import LiteLLMProvider


def configuration():
    values = {
        "review.maximum_model_calls": 8,
        "review.time_budget": 900,
        "review.output_tokens": 8192,
    }
    return SimpleNamespace(configuration=SimpleNamespace(value=values.get))


def test_paid_response_survives_worker_restart_without_duplicate_usage(
    tmp_path, monkeypatch
):
    captured = json.loads(
        (Path(__file__).parents[1] / "fixtures/deepseek/overview.json").read_text()
    )
    completion = Mock(return_value=litellm.ModelResponse(**captured))
    usage = Mock()
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr("src.core.usage.record_completion", usage)
    engine = create_engine("sqlite:///" + str(tmp_path / "continuation.sqlite"))
    store = SQLReviewWork(engine, create_schema=True)
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": []},
        maximum_calls=8,
    )

    class Reviewer:
        @review_session
        def review(self, request):
            budget = current_budget()
            budget.work, budget.run_id = SQLReviewWork(engine), "run"
            provider = LiteLLMProvider("deepseek/deepseek-chat", 131072)
            answer = provider._completion(
                model=provider.model,
                messages=[{"role": "user", "content": "Summarize this change"}],
            )
            provider._spent(answer, "review-overview")
            return answer.choices[0].message.content

    assert Reviewer().review(configuration()) == Reviewer().review(configuration())
    assert completion.call_count == 1
    assert usage.call_count == 1
    assert store.progress("run")["model_calls"] == 1
    store.cancel("run")
    with pytest.raises(RuntimeError, match="no longer active"):
        Reviewer().review(configuration())
    assert completion.call_count == 1


def test_complete_prompt_overflow_fails_before_paid_request(monkeypatch):
    completion = Mock()
    monkeypatch.setattr(litellm, "completion", completion)

    class Reviewer:
        @review_session
        def review(self, request):
            return LiteLLMProvider("deepseek/deepseek-chat", 16)._completion(
                model="deepseek/deepseek-chat",
                messages=[{"role": "user", "content": "Review this change"}],
            )

    with pytest.raises(PartitionCapacityError, match="complete model prompt"):
        Reviewer().review(configuration())
    completion.assert_not_called()


def test_durable_window_yields_without_resetting_total_budget(tmp_path, monkeypatch):
    from src.core.review_budget import ReviewBudget

    now = [0]
    monkeypatch.setattr("src.core.review_budget.monotonic", lambda: now[0])
    budget = ReviewBudget(configuration().configuration)
    budget.work = object()
    now[0] = 850
    with pytest.raises(ReviewContinuation):
        budget.reserve()
    assert budget.continuation
    assert not budget.exhausted
    assert budget.calls == 0


def test_large_allowance_cannot_exceed_operator_ceiling(monkeypatch):
    from src.core.review_budget import ReviewBudget, ReviewBudgetExceeded

    monkeypatch.setenv("SOURCEANT_LARGE_REVIEW_MAXIMUM_CALLS", "256")
    config = SimpleNamespace(
        value=lambda key: {"review.large_change_maximum_model_calls": 1000}.get(key)
    )
    budget = ReviewBudget()
    budget.plan_parts(30, config, calls_per_part=6)
    assert budget.maximum_calls <= 256
    with pytest.raises(ReviewBudgetExceeded, match="before discovery"):
        budget.plan_parts(100, config, calls_per_part=4)
    assert budget.calls == 0


def test_superseded_run_stops_cached_replay_before_model_request(tmp_path, monkeypatch):
    from src.core.review_budget import ReviewStopped

    captured = json.loads(
        (Path(__file__).parents[1] / "fixtures/deepseek/overview.json").read_text()
    )
    completion = Mock(return_value=litellm.ModelResponse(**captured))
    monkeypatch.setattr(litellm, "completion", completion)
    engine = create_engine("sqlite:///" + str(tmp_path / "superseded.sqlite"))
    store = SQLReviewWork(engine, create_schema=True)
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": []},
        maximum_calls=8,
    )
    stopped = [False]

    class Reviewer:
        @review_session
        def review(self, request):
            budget = current_budget()
            budget.work, budget.run_id = SQLReviewWork(engine), "run"
            budget.stopped = lambda: stopped[0]
            return LiteLLMProvider("deepseek/deepseek-chat", 131072)._completion(
                model="deepseek/deepseek-chat",
                messages=[{"role": "user", "content": "Review the pinned revision"}],
            )

    Reviewer().review(configuration())
    stopped[0] = True
    with pytest.raises(ReviewStopped):
        Reviewer().review(configuration())
    assert completion.call_count == 1
    assert store.progress("run")["state"] == "cancelled"


def test_validation_retry_is_fresh_and_both_attempts_survive_continuation(
    tmp_path, monkeypatch
):
    from src.models.code_review import CodeReview

    fixtures = Path(__file__).parents[1] / "fixtures/deepseek"
    completion = Mock(
        side_effect=[
            litellm.ModelResponse(**json.loads((fixtures / name).read_text()))
            for name in ("overview.json", "review.json")
        ]
    )
    usage = Mock()
    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr("src.core.usage.record_completion", usage)
    engine = create_engine("sqlite:///" + str(tmp_path / "validation-retry.sqlite"))
    store = SQLReviewWork(engine, create_schema=True)
    store.open(
        "run",
        workspace="ours",
        repository="owner/repo",
        revision="head",
        plan={"parts": []},
        maximum_calls=8,
    )

    class Reviewer:
        @review_session
        def review(self, request):
            budget = current_budget()
            budget.work, budget.run_id = SQLReviewWork(engine), "run"
            provider = LiteLLMProvider("deepseek/deepseek-chat", 131072)
            return provider._validated_response(
                lambda: provider._completion(
                    model=provider.model,
                    messages=[{"role": "user", "content": "Inspect this change"}],
                ),
                CodeReview,
                "review",
            )

    assert Reviewer().review(configuration()) == Reviewer().review(configuration())
    assert completion.call_count == 2
    assert usage.call_count == 2
    assert store.progress("run")["model_calls"] == 2
