import os
import pytest

from src.llms.errors import LLMError
from unittest.mock import patch, MagicMock

from src.llms.litellm_provider import LiteLLMProvider
from src.prompts.prompts import Prompts
from src.tests.unit.helpers import make_diff as _make_diff
from src.utils.diff_parser import parse_diff
from src.models.code_review import (
    CodeReview,
    Verdict,
    CodeReviewScores,
    CodeReviewSummary,
    CodeReviewOverview,
)


@pytest.fixture
def provider():
    return LiteLLMProvider(
        model="gemini/gemini-2.5-flash",
        token_limit=1000000,
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
    )
    assert provider.model == "deepseek/deepseek-chat"
    assert provider.token_limit == 500000


def test_token_limit_property(provider):
    assert provider.token_limit == 1000000


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
    result = provider.generate_code_review(diff)

    assert isinstance(result, CodeReview)
    assert result.summary == summary
    assert result.verdict == Verdict.COMMENT
    mock_completion.completion.assert_called_once()


def test_generate_code_review_includes_bounded_structural_context(
    provider, mock_completion
):
    review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[])
    mock_completion.completion.return_value = _make_completion_response(
        review.model_dump_json()
    )

    provider.generate_code_review(
        "+ changed", code_context='{"nodes":[{"id":"symbol:run"}]}'
    )

    messages = mock_completion.completion.call_args.kwargs["messages"]
    assert '"id":"symbol:run"' in messages[1]["content"]


def test_generate_code_review_preserves_llm_verdict(provider, mock_completion):
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

    result = provider.generate_code_review("- old\n+ new")
    assert result.verdict == Verdict.REQUEST_CHANGES


def test_generate_code_review_preserves_llm_verdict_approve(provider, mock_completion):
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

    result = provider.generate_code_review("- old\n+ new")
    assert result.verdict == Verdict.APPROVE


def test_generate_code_review_api_error(provider, mock_completion):
    mock_completion.completion.side_effect = Exception("API is down")

    result = provider.generate_code_review("- old\n+ new")
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


def test_generate_text_names_the_provider_failure(provider, mock_completion):
    class AuthenticationError(Exception):
        pass

    mock_completion.completion.side_effect = AuthenticationError(
        "API key not valid. Please pass a valid API key."
    )

    with pytest.raises(LLMError) as raised:
        provider.generate_text("Say hello")

    assert "AuthenticationError" in str(raised.value)
    assert "API key not valid" in str(raised.value)
    assert isinstance(raised.value.cause, AuthenticationError)


def test_generate_text_does_not_answer_with_an_empty_string(provider, mock_completion):
    mock_completion.completion.side_effect = Exception("fail")

    with pytest.raises(LLMError):
        provider.generate_text("Say hello")


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


class TestSystemUserMessageStructure:
    def test_sends_system_and_user_messages(self, provider, mock_completion):
        mock_completion.completion.return_value = _make_completion_response(
            '{"verdict": "COMMENT", "code_suggestions": []}'
        )
        mock_completion.token_counter.return_value = 10

        provider.generate_code_review(diff="some diff")

        call_args = mock_completion.completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_system_message_contains_review_system_prompt(
        self, provider, mock_completion
    ):
        mock_completion.completion.return_value = _make_completion_response(
            '{"verdict": "COMMENT", "code_suggestions": []}'
        )
        mock_completion.token_counter.return_value = 10

        provider.generate_code_review(diff="some diff")

        call_args = mock_completion.completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert messages[0]["content"] == Prompts.REVIEW_SYSTEM_PROMPT


class TestPrMetadataInPrompt:
    def test_pr_metadata_included_in_user_message(self, provider, mock_completion):
        mock_completion.completion.return_value = _make_completion_response(
            '{"verdict": "COMMENT", "code_suggestions": []}'
        )
        mock_completion.token_counter.return_value = 10

        metadata = {
            "title": "Test PR",
            "number": 99,
            "description": "A test pull request",
            "base_ref": "main",
            "head_ref": "feature/test",
        }
        provider.generate_code_review(diff="some diff", pr_metadata=metadata)

        call_args = mock_completion.completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_content = messages[1]["content"]
        assert "Test PR" in user_content
        assert "99" in user_content
        assert "feature/test → main" in user_content

    def test_no_metadata_shows_fallback(self, provider, mock_completion):
        mock_completion.completion.return_value = _make_completion_response(
            '{"verdict": "COMMENT", "code_suggestions": []}'
        )
        mock_completion.token_counter.return_value = 10

        provider.generate_code_review(diff="some diff")

        call_args = mock_completion.completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_content = messages[1]["content"]
        assert "No PR metadata available." in user_content


class TestDecoupledDiffInReview:
    def test_uses_decoupled_format_when_parsed_files_available(
        self, provider, mock_completion
    ):
        mock_completion.completion.return_value = _make_completion_response(
            '{"verdict": "COMMENT", "code_suggestions": []}'
        )
        mock_completion.token_counter.return_value = 10

        before = ["a = 1"]
        after = ["a = 10"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)

        provider.generate_code_review(diff=diff_text, parsed_files=parsed)

        call_args = mock_completion.completion.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        user_content = messages[1]["content"]
        assert "__old hunk__" in user_content or "__new hunk__" in user_content


def test_summary_with_full_change_context_uses_the_existing_format(
    provider, mock_completion
):
    from pathlib import Path

    full_diff = (
        Path(__file__).parents[1] / "fixtures/review-overview/full.diff"
    ).read_text()
    summary = CodeReviewSummary(
        overview="Adds system suggestions and requirement priority.",
        key_improvements=["Groups related repositories."],
        minor_suggestions=[],
        critical_issues=[],
    )
    mock_completion.completion.return_value = _make_completion_response(
        summary.model_dump_json()
    )
    result = provider.generate_summary([], change_context=full_diff)
    assert result == summary
    call = mock_completion.completion.call_args.kwargs
    assert call["response_format"] is CodeReviewOverview
    prompt = call["messages"][0]["content"]
    assert full_diff in prompt
    assert "**JSON format**" in prompt
    assert "**GitHub-flavored Markdown**" in prompt
    assert '"key_improvements"' in prompt


class TestWhatAModelNeedsToAuthenticateWith:
    """Which variable holds a key is a fact about the provider, so litellm
    answers it rather than a table kept here."""

    def test_a_model_with_nothing_to_authenticate_with_says_what_is_missing(
        self, monkeypatch
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        model = LiteLLMProvider(model="anthropic/claude-opus-5", token_limit=1000)

        assert model.missing_credentials() == ["ANTHROPIC_API_KEY"]

    def test_a_key_given_for_the_call_is_enough(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        model = LiteLLMProvider(
            model="anthropic/claude-opus-5", token_limit=1000, api_key="whatever"
        )

        assert model.missing_credentials() == []

    def test_a_key_in_the_environment_is_enough(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "whatever")
        model = LiteLLMProvider(model="anthropic/claude-opus-5", token_limit=1000)

        assert model.missing_credentials() == []

    def test_a_model_litellm_does_not_know_is_not_reported_as_missing_one(self):
        """An unfamiliar name must not stop a deployment running its own model."""
        model = LiteLLMProvider(
            model="something-nobody-here-has-heard-of", token_limit=1
        )

        assert model.missing_credentials() == []

    def test_a_check_that_fails_does_not_stop_the_review(self):
        model = LiteLLMProvider(model="anthropic/claude-opus-5", token_limit=1000)

        with patch(
            "src.llms.litellm_provider.litellm.validate_environment",
            side_effect=RuntimeError("no"),
        ):
            assert model.missing_credentials() == []


class TestWhatThePassesOfOneReviewShare:
    """Providers cache a matching prefix, so what does not change goes first."""

    @staticmethod
    def _sent(mock_litellm):
        """What reached the model, whether or not the halves were marked."""
        content = mock_litellm.completion.call_args.kwargs["messages"][1]["content"]
        if isinstance(content, str):
            return content
        return "".join(block["text"] for block in content)

    def _read_a_batch(self, provider, diff, comments, structure):
        return provider.generate_code_review(
            diff=diff,
            pr_metadata={"title": "Group repositories", "number": 7},
            existing_comments=comments,
            previous_summary="What this review already said.",
            code_context=structure,
            requirements="## Requirements\n\nOne requirement.\n",
            knowledge="## Knowledge\n\nOne thing worth knowing.\n",
            impact="## Impact\n\nOne thing this reaches.\n",
            related_code="## What This Review Went Looking For\n\nOne search.",
        )

    def test_two_batches_share_a_prefix_holding_all_the_shared_context(
        self, provider, mock_completion
    ):
        self._read_a_batch(
            provider, "diff of the first batch", [{"path": "a.py", "body": "x"}], "A"
        )
        first = self._sent(mock_completion)

        self._read_a_batch(
            provider, "diff of the second batch", [{"path": "b.py", "body": "y"}], "B"
        )
        second = self._sent(mock_completion)

        shared = first[: len(os.path.commonprefix([first, second]))]
        assert "Group repositories" in shared
        assert "What this review already said." in shared
        assert "One requirement." in shared
        assert "One thing worth knowing." in shared
        assert "One thing this reaches." in shared
        assert "One search." in shared

    def test_what_differs_between_batches_is_all_after_the_shared_part(
        self, provider, mock_completion
    ):
        self._read_a_batch(
            provider, "diff of the first batch", [{"path": "a.py", "body": "x"}], "A"
        )
        first = self._sent(mock_completion)

        self._read_a_batch(
            provider, "diff of the second batch", [{"path": "b.py", "body": "y"}], "B"
        )
        second = self._sent(mock_completion)

        shared = os.path.commonprefix([first, second])
        assert "diff of the first batch" not in shared
        assert "a.py" not in shared


class TestMarkingWhereTheSettledHalfEnds:
    """Marked by default, and turned off where the provider's store costs
    more than the reading it saves."""

    @staticmethod
    def _settled():
        return "settled " * 8000

    def test_a_deployment_that_wants_none_of_it_gets_none_of_it(self):
        model = LiteLLMProvider(
            model="anthropic/claude-opus-5", token_limit=100000, cache_prompts=False
        )

        assert isinstance(model._layered(self._settled(), "changing"), str)

    def test_the_boundary_is_marked_and_nothing_after_it_is(self):
        model = LiteLLMProvider(model="anthropic/claude-opus-5", token_limit=100000)

        blocks = model._layered(self._settled(), "changing")

        assert [one["cache_control"] for one in blocks if "cache_control" in one] == [
            {"type": "ephemeral"}
        ]
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert blocks[1]["text"] == "changing"

    def test_a_prompt_under_every_providers_floor_is_not_marked(self):
        model = LiteLLMProvider(model="anthropic/claude-opus-5", token_limit=100000)

        assert isinstance(model._layered("too short to be worth it", "changing"), str)

    def test_a_model_that_cannot_be_told_is_not_told(self):
        model = LiteLLMProvider(
            model="something-nobody-here-has-heard-of", token_limit=100000
        )

        assert isinstance(model._layered(self._settled(), "changing"), str)


class TestAProviderRefusingToKeepAPrompt:
    """A cache the provider will not build must not cost the review."""

    @staticmethod
    def _marked(provider):
        return [
            {"type": "text", "text": "settled", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "changing"},
        ]

    def test_a_refused_cache_is_asked_again_without_the_mark(
        self, provider, mock_completion
    ):
        review = CodeReview(verdict=Verdict.COMMENT, code_suggestions=[])
        mock_completion.completion.side_effect = [
            Exception("cached content is too small"),
            _make_completion_response(review.model_dump_json()),
        ]

        with patch.object(provider, "_layered", return_value=self._marked(provider)):
            answered = provider.generate_code_review("+ changed")

        assert answered is not None
        assert mock_completion.completion.call_count == 2
        second = mock_completion.completion.call_args.kwargs["messages"][1]["content"]
        assert isinstance(second, str)
        assert "+ changed" in second

    def test_an_unmarked_prompt_that_fails_is_not_asked_twice(
        self, provider, mock_completion
    ):
        mock_completion.completion.side_effect = Exception("the provider is down")

        assert provider.generate_code_review("+ changed") is None
        assert mock_completion.completion.call_count == 1
