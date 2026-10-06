import json
from pathlib import Path
from unittest.mock import patch

import pytest
from litellm import ModelResponse

from src.core.model.settings import SettingsLLMSource
from src.core.review.checkout import CheckoutConfiguration
from src.llms.litellm_provider import LiteLLMProvider


def captured():
    return ModelResponse(
        **json.loads(
            (Path(__file__).parents[1] / "fixtures/deepseek/review.json").read_text()
        )
    )


def test_output_and_reasoning_settings_reach_the_model_request():
    configuration = CheckoutConfiguration(
        overrides={
            "model.name": "deepseek/deepseek-flash",
            "model.max_output_tokens": 16384,
            "model.reasoning_effort": "low",
            "review.reuse_responses_days": 0,
        }
    )
    provider = SettingsLLMSource().provider_for(configuration)
    with patch(
        "src.llms.litellm_provider.litellm.completion", return_value=captured()
    ) as completion:
        provider.generate_text("Return the review", purpose="review")
    assert completion.call_args.kwargs["max_tokens"] == 16384
    assert completion.call_args.kwargs["reasoning_effort"] == "low"
    assert completion.call_args.kwargs["extra_body"]["reasoning_effort"] == "low"


def test_profile_can_override_or_inherit_the_output_budget():
    configuration = CheckoutConfiguration(
        overrides={
            "model.max_output_tokens": 16384,
            "model.reasoning_effort": "low",
            "model.profiles": {
                "inherited": {"name": "deepseek/deepseek-flash"},
                "specialist": {
                    "name": "deepseek/deepseek-v4-pro",
                    "max_output_tokens": 8192,
                    "reasoning_effort": "high",
                },
            },
            "model.purposes": {"review-evaluation": ["inherited", "specialist"]},
        }
    )
    providers = SettingsLLMSource().providers_for(configuration, "review-evaluation")
    with patch(
        "src.llms.litellm_provider.litellm.completion", return_value=captured()
    ) as completion:
        for provider in providers:
            provider.generate_text("Evaluate the findings", purpose="review-evaluation")
    first, second = [call.kwargs for call in completion.call_args_list]
    assert (first["max_tokens"], first["reasoning_effort"]) == (16384, "low")
    assert (second["max_tokens"], second["reasoning_effort"]) == (
        8192,
        "high",
    )


def test_unsupported_reasoning_fails_before_calling_the_provider():
    provider = LiteLLMProvider("openai/gpt-4.1", 131072, reasoning_effort="low")
    with patch("src.llms.litellm_provider.litellm.completion") as completion:
        with pytest.raises(ValueError, match="does not support reasoning effort"):
            provider._completion(model=provider.model, messages=[])
    completion.assert_not_called()


def test_unset_budget_preserves_provider_defaults():
    provider = LiteLLMProvider("deepseek/deepseek-flash", 131072)
    with patch(
        "src.llms.litellm_provider.litellm.completion", return_value=captured()
    ) as completion:
        provider.generate_text("Return the review", purpose="review")
    assert "max_tokens" not in completion.call_args.kwargs
    assert "max_completion_tokens" not in completion.call_args.kwargs
    assert "reasoning_effort" not in completion.call_args.kwargs


def test_truncated_structured_output_is_not_retried_with_the_same_budget():
    from src.llms.errors import LLMError
    from src.models.code_review import CodeReview

    sample = (
        Path(__file__).parents[1] / "fixtures/deepseek/output-budget-exhausted.json"
    )
    response = ModelResponse(**json.loads(sample.read_text()))
    provider = LiteLLMProvider("deepseek/deepseek-flash", 131072)
    with patch(
        "src.llms.litellm_provider.litellm.completion", return_value=response
    ) as completion:
        with pytest.raises(LLMError, match="output budget exhausted"):
            provider._validated_response(
                lambda: provider._completion(model=provider.model, messages=[]),
                CodeReview,
                "review",
            )
    completion.assert_called_once()


def test_structured_generation_requests_json_and_validates_captured_output():
    from src.models.code_review import CodeReview

    provider = LiteLLMProvider("deepseek/deepseek-flash", 131072)
    with patch(
        "src.llms.litellm_provider.litellm.completion", return_value=captured()
    ) as completion:
        written = provider.generate_structured(
            "Evaluate the review", CodeReview, purpose="review-evaluation"
        )
    CodeReview.model_validate_json(written)
    assert completion.call_args.kwargs["response_format"] == {"type": "json_object"}
