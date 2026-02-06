import pytest
from unittest.mock import patch, MagicMock

from src.llms.litellm_provider import LiteLLMProvider
from src.models.code_review import (
    CodeReview,
    Verdict,
    CodeReviewScores,
    CodeReviewSummary,
)


@pytest.fixture
def provider():
    return LiteLLMProvider(
        model="gemini/gemini-2.5-flash",
        token_limit=1000000,
        uploads_enabled=False,
    )


@pytest.fixture
def mock_completion():
    with patch("src.llms.litellm_provider.litellm") as mock_litellm:
        yield mock_litellm


def _make_completion_response(content: str):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    return mock_response


def test_init_sets_model_and_config():
    provider = LiteLLMProvider(
        model="deepseek/deepseek-chat",
        token_limit=500000,
        uploads_enabled=True,
    )
    assert provider.model == "deepseek/deepseek-chat"
    assert provider.token_limit == 500000
    assert provider.uploads_enabled is True


def test_token_limit_property(provider):
    assert provider.token_limit == 1000000


def test_uploads_enabled_property(provider):
    assert provider.uploads_enabled is False


def test_count_tokens_delegates_to_litellm(provider, mock_completion):
    mock_completion.token_counter.return_value = 42
    result = provider.count_tokens("hello world")
    assert result == 42
    mock_completion.token_counter.assert_called_once_with(
        model="gemini/gemini-2.5-flash", text="hello world"
    )


def test_generate_code_review_success(provider, mock_completion):
    summary = CodeReviewSummary(
        overview="Great job!",
        key_improvements=[],
        minor_suggestions=[],
        critical_issues=[],
    )
    review = CodeReview(
        summary=summary,
        verdict=Verdict.COMMENT,
        code_suggestions=[],
        scores=CodeReviewScores(
            correctness=9,
            clarity=8,
            maintainability=7,
            security=9,
            performance=8,
        ),
    )
    mock_completion.completion.return_value = _make_completion_response(
        review.model_dump_json()
    )

    diff = "- old code\n+ new code"
    result = provider.generate_code_review(diff, file_paths=None)

    assert isinstance(result, CodeReview)
    assert result.summary == summary
    assert result.verdict == Verdict.APPROVE
    mock_completion.completion.assert_called_once()


def test_generate_code_review_verdict_approve_on_high_score(provider, mock_completion):
    review = CodeReview(
        summary=CodeReviewSummary(
            overview="Decent code.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        ),
        verdict=Verdict.REQUEST_CHANGES,
        code_suggestions=[],
        scores=CodeReviewScores(
            correctness=8,
            clarity=7,
            maintainability=6,
            security=8,
            performance=7,
        ),
    )
    mock_completion.completion.return_value = _make_completion_response(
        review.model_dump_json()
    )

    result = provider.generate_code_review("- old\n+ new", file_paths=None)
    assert result.verdict == Verdict.APPROVE


def test_generate_code_review_verdict_request_changes_on_low_score(
    provider, mock_completion
):
    review = CodeReview(
        summary=CodeReviewSummary(
            overview="Needs work.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        ),
        verdict=Verdict.APPROVE,
        code_suggestions=[],
        scores=CodeReviewScores(
            correctness=2,
            clarity=2,
            maintainability=3,
            security=4,
            performance=3,
        ),
    )
    mock_completion.completion.return_value = _make_completion_response(
        review.model_dump_json()
    )

    result = provider.generate_code_review("- old\n+ new", file_paths=None)
    assert result.verdict == Verdict.REQUEST_CHANGES


def test_generate_code_review_api_error(provider, mock_completion):
    mock_completion.completion.side_effect = Exception("API is down")

    result = provider.generate_code_review("- old\n+ new", file_paths=None)
    assert result is None


def test_generate_summary_empty_suggestions(provider):
    result = provider.generate_summary([])
    assert isinstance(result, CodeReviewSummary)
    assert result.key_improvements == []


def test_generate_summary_as_text_empty(provider):
    result = provider.generate_summary([], as_text=True)
    assert isinstance(result, str)


def test_generate_text_success(provider, mock_completion):
    mock_completion.completion.return_value = _make_completion_response("Hello there")
    result = provider.generate_text("Say hello")
    assert result == "Hello there"


def test_generate_text_error(provider, mock_completion):
    mock_completion.completion.side_effect = Exception("fail")
    result = provider.generate_text("Say hello")
    assert result == ""


def test_is_summary_different_returns_true(provider, mock_completion):
    mock_completion.completion.return_value = _make_completion_response("DIFFERENT")
    assert provider.is_summary_different("old summary", "new summary") is True


def test_is_summary_different_returns_false(provider, mock_completion):
    mock_completion.completion.return_value = _make_completion_response("SAME")
    assert provider.is_summary_different("same", "same") is False


def test_is_summary_different_error_defaults_to_true(provider, mock_completion):
    mock_completion.completion.side_effect = Exception("fail")
    assert provider.is_summary_different("a", "b") is True


def test_model_is_configurable():
    provider = LiteLLMProvider(
        model="anthropic/claude-sonnet-4-5-20250929",
        token_limit=200000,
    )
    assert provider.model == "anthropic/claude-sonnet-4-5-20250929"
    assert provider.token_limit == 200000
