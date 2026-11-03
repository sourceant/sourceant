from types import SimpleNamespace

import pytest

from src.core.parallel import model_call, parallel_map
from src.core.review_budget import ReviewBudget, ReviewBudgetExceeded
from src.core.review_performance import current_budget, review_session


def change(**values):
    defaults = {
        "review.maximum_model_calls": 2,
        "review.time_budget": 60,
        "review.output_tokens": 8192,
    }
    return SimpleNamespace(
        configuration=SimpleNamespace(
            value=lambda key: values.get(key, defaults.get(key))
        )
    )


def test_the_call_limit_is_shared_by_parallel_readings():
    class Reviewer:
        @review_session
        def review(self, request):
            def ask(_):
                with model_call("test-model"):
                    return 1

            return parallel_map(ask, range(10), workers=6)

    with pytest.raises(ReviewBudgetExceeded, match="model-call budget"):
        Reviewer().review(change())


def test_swallowing_a_budget_error_cannot_publish_a_partial_review():
    class Reviewer:
        @review_session
        def review(self, request):
            try:
                for _ in range(3):
                    with model_call("test-model"):
                        pass
            except ReviewBudgetExceeded:
                return "partial"

    with pytest.raises(ReviewBudgetExceeded, match="no partial review"):
        Reviewer().review(change())


def test_model_calls_cannot_start_after_the_time_budget(monkeypatch):
    now = [0]
    monkeypatch.setattr("src.core.review_budget.monotonic", lambda: now[0])
    budget = ReviewBudget(change().configuration)
    now[0] = 61
    with pytest.raises(ReviewBudgetExceeded, match="time budget"):
        budget.reserve()
    assert budget.calls == 0


def test_review_calls_receive_a_request_timeout_and_output_budget(monkeypatch):
    from src.llms.litellm_provider import LiteLLMProvider

    class Captured(Exception):
        pass

    called = {}

    def completion(**kwargs):
        called.update(kwargs)
        raise Captured()

    monkeypatch.setattr("litellm.completion", completion)

    class Reviewer:
        @review_session
        def review(self, request):
            assert current_budget().output_tokens == 8192
            return LiteLLMProvider("openai/gpt-4o", 100000)._completion(
                model="openai/gpt-4o", messages=[]
            )

    with pytest.raises(Captured):
        Reviewer().review(change())
    assert 0 < called["timeout"] <= 60
    assert called.get("max_tokens", called.get("max_completion_tokens")) == 8192
