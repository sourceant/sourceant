import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from litellm import ModelResponse
from litellm.exceptions import BadRequestError

from src.llms.errors import LLMError
from src.llms.litellm_provider import LiteLLMProvider
from src.models.code_review import CodeReviewFindings, CodeReviewOverview

FIXTURES = Path(__file__).parents[1] / "fixtures/deepseek"


def response(name="review"):
    return ModelResponse(**json.loads((FIXTURES / f"{name}.json").read_text()))


def unsupported():
    return BadRequestError(
        message=(FIXTURES / "unsupported-format.json").read_text(),
        model="deepseek-v4-flash",
        llm_provider="deepseek",
    )


@pytest.fixture
def completion(monkeypatch):
    call = Mock(return_value=response())
    monkeypatch.setattr("litellm.completion", call)
    monkeypatch.setattr("litellm.supports_response_schema", lambda **_: True)
    monkeypatch.setattr(
        "litellm.get_supported_openai_params", lambda **_: ["response_format"]
    )
    return call


def model(name="deepseek/deepseek-v4-flash"):
    return LiteLLMProvider(model=name, token_limit=100000)


def test_deepseek_uses_json_despite_advertised_schema_support(completion):
    review = model().generate_code_review("+ changed")
    assert review.verdict.value == "COMMENT"
    completion.assert_called_once()
    sent = completion.call_args.kwargs
    assert sent["response_format"] == {"type": "json_object"}
    assert (
        json.dumps(CodeReviewFindings.model_json_schema())
        in sent["messages"][-1]["content"]
    )


def test_native_schema_is_kept_for_supported_providers(completion):
    model("gemini/gemini-2.5-flash").generate_code_review("+ changed")
    assert completion.call_args.kwargs["response_format"] is CodeReviewFindings


def test_explicit_format_rejection_falls_back_once_and_is_remembered(completion):
    completion.side_effect = [unsupported(), response(), response()]
    provider = model("gemini/gemini-2.5-flash")
    provider.generate_code_review("+ first change")
    provider.generate_code_review("+ second change")
    assert completion.call_count == 3
    assert completion.call_args_list[0].kwargs["response_format"] is CodeReviewFindings
    for call in completion.call_args_list[1:]:
        assert call.kwargs["response_format"] == {"type": "json_object"}


def test_failed_fallback_is_not_retried(completion):
    completion.side_effect = unsupported()
    with pytest.raises(LLMError):
        model("gemini/gemini-2.5-flash").generate_code_review("+ changed")
    assert completion.call_count == 2


@pytest.mark.parametrize("failure", [TimeoutError(), ConnectionError()])
def test_transport_failures_do_not_trigger_format_fallback(completion, failure):
    completion.side_effect = failure
    with pytest.raises(LLMError) as raised:
        model("gemini/gemini-2.5-flash").generate_code_review("+ changed")
    assert raised.value.cause is failure
    completion.assert_called_once()


def test_invalid_structure_fails_after_one_retry(completion):
    completion.return_value = response("overview")
    with pytest.raises(LLMError, match="after one retry"):
        model().generate_code_review("+ changed")
    assert completion.call_count == 2


def test_summary_uses_the_same_provider_compatibility(completion):
    completion.return_value = response("overview")
    summary = model().generate_summary([], change_context="+ changed")
    assert summary.overview == "The change updates a function."
    sent = completion.call_args.kwargs
    assert sent["response_format"] == {"type": "json_object"}
    assert (
        json.dumps(CodeReviewOverview.model_json_schema())
        in sent["messages"][-1]["content"]
    )


def test_unknown_capability_uses_json_and_validates_locally(completion, monkeypatch):
    monkeypatch.setattr("litellm.supports_response_schema", lambda **_: False)
    model("gemini/gemini-2.5-flash").generate_code_review("+ changed")
    assert completion.call_args.kwargs["response_format"] == {"type": "json_object"}


def test_provider_without_json_mode_gets_schema_in_prompt(completion, monkeypatch):
    monkeypatch.setattr("litellm.supports_response_schema", lambda **_: False)
    monkeypatch.setattr("litellm.get_supported_openai_params", lambda **_: [])
    model("gemini/gemini-2.5-flash").generate_code_review("+ changed")
    assert "response_format" not in completion.call_args.kwargs


def test_summary_retries_only_invalid_response_and_counts_both_calls(completion):
    completion.side_effect = [response("review"), response("overview")]
    provider = model()
    provider._spent = Mock()
    summary = provider.generate_summary([], change_context="+ changed")
    assert summary.overview == "The change updates a function."
    assert completion.call_count == 2
    assert provider._spent.call_count == 2
    assert all(call.args[1] == "summary" for call in provider._spent.call_args_list)


def test_summary_stops_after_second_invalid_response(completion):
    completion.return_value = response("review")
    with pytest.raises(LLMError, match="Invalid summary output after one retry"):
        model().generate_summary([], change_context="+ changed")
    assert completion.call_count == 2


def test_summary_does_not_retry_provider_errors(completion):
    completion.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        model().generate_summary([], change_context="+ changed")
    completion.assert_called_once()
