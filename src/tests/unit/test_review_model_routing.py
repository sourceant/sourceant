from types import SimpleNamespace

import pytest

from src.core.model import ModelRouter, provider_for, providers_for
from src.core.model.settings import SettingsLLMSource
from src.core.review import ReviewInput
from src.core.review.planning import SettingsReviewPlanSource
from src.core.services import ServiceRegistry


def configuration(values):
    return SimpleNamespace(
        value=lambda key: values.get(key),
        with_workspace=lambda: configuration(values),
        attribution=lambda: {},
    )


def test_routing_uses_each_selected_profile_and_bound_credentials(monkeypatch):
    monkeypatch.setattr(
        "src.core.model.settings.LiteLLMProvider", lambda **options: options
    )
    config = configuration(
        {
            "model.purposes": {"review": ["first", "second"]},
            "model.profiles": {
                "first": {"name": "openai/first", "token_limit": 1234},
                "second": {"name": "openai/second"},
            },
            "model.profile_credentials": {
                "first": {"model": "openai/first", "api_key": "your-api-key-here"},
            },
        }
    )
    selected = SettingsLLMSource().providers_for(config, "review")
    assert [one["model"] for one in selected] == ["openai/first", "openai/second"]
    assert selected[0]["api_key"] == "your-api-key-here"
    assert selected[0]["token_limit"] == 1234
    assert selected[1]["api_key"] is None


def test_profile_override_cannot_borrow_credentials_for_another_model():
    config = configuration(
        {
            "model.purposes": {"review": ["first"]},
            "model.profiles": {"first": {"name": "openai/new-model"}},
            "model.profile_credentials": {
                "first": {"model": "openai/old-model", "api_key": "your-api-key-here"}
            },
        }
    )
    with pytest.raises(ValueError, match="Credentials do not match"):
        SettingsLLMSource().providers_for(config, "review")


def test_premium_uses_registered_router_and_configured_repetitions():
    reviewer = object()
    discovery = SimpleNamespace(missing_credentials=lambda: False)
    evaluator = SimpleNamespace(missing_credentials=lambda: False)

    class Router:
        def providers_for(self, configuration, purpose):
            return (discovery,) if purpose == "review" else (evaluator,)

    services = ServiceRegistry()
    services.register(ModelRouter, Router(), provider="test")
    config = configuration(
        {
            "review.plan": "premium",
            "model.purposes": {"review": ["a"], "review-evaluation": ["b"]},
            "review.discovery_passes": 3,
            "review.evaluation_passes": 2,
            "review.minimum_support": 2,
            "review.maximum_rejections": 0,
            "review.concurrency": 6,
        }
    )
    assert providers_for(config, "review", services) == (discovery,)
    assert provider_for(config, services, purpose="review-evaluation") is evaluator
    plan = SettingsReviewPlanSource(services).plan_for(
        ReviewInput(SimpleNamespace(configuration=config)),
        reviewer,
        None,
    )
    assert plan.reviewers[0].provider is discovery
    assert plan.reviewers[0].repetitions == 3
    assert plan.evaluators[0].evaluator.provider is evaluator
    assert plan.evaluators[0].repetitions == 2
    assert plan.minimum_support == 2
