import difflib
import pytest
from unittest.mock import patch, MagicMock

from src.llms.litellm_provider import LiteLLMProvider, CONTEXT_TOKEN_BUDGET
from src.prompts.prompts import Prompts
from src.utils.diff_parser import parse_diff
from src.models.code_review import (
    CodeReview,
    Verdict,
    CodeReviewScores,
    CodeReviewSummary,
)


def _make_diff(before_lines, after_lines, filename="test_file.py"):
    return "".join(
        difflib.unified_diff(
            [l + "\n" for l in before_lines],
            [l + "\n" for l in after_lines],
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
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
    assert result.verdict == Verdict.COMMENT
    mock_completion.completion.assert_called_once()


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

    result = provider.generate_code_review("- old\n+ new", file_paths=None)
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

    result = provider.generate_code_review("- old\n+ new", file_paths=None)
    assert result.verdict == Verdict.APPROVE


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


class TestContextWindowCapping:
    def test_build_file_context_sorts_by_changes(self, provider, tmp_path):
        before_small = ["a = 1", "b = 2"]
        after_small = ["a = 1", "b = 20"]
        diff_small = _make_diff(before_small, after_small, "small.py")

        before_big = ["x = 1", "y = 2", "z = 3"]
        after_big = ["x = 10", "y = 20", "z = 30"]
        diff_big = _make_diff(before_big, after_big, "big.py")

        combined_diff = diff_small + diff_big
        parsed = parse_diff(combined_diff)

        small_file = tmp_path / "small.py"
        small_file.write_text("a = 1\nb = 2\n")
        big_file = tmp_path / "big.py"
        big_file.write_text("x = 1\ny = 2\nz = 3\n")

        with patch.object(provider, "count_tokens", return_value=100):
            result = provider._build_file_context(
                parsed, [str(small_file), str(big_file)]
            )

        big_pos = result.find("big.py")
        small_pos = result.find("small.py")
        assert big_pos < small_pos, "File with more changes should appear first"

    def test_build_file_context_respects_token_budget(self, provider, tmp_path):
        before = ["a = 1"]
        after = ["a = 10"]
        diff_text = _make_diff(before, after, "file1.py")
        parsed = parse_diff(diff_text)

        file1 = tmp_path / "file1.py"
        file1.write_text("a = 1\n")

        with patch.object(
            provider, "count_tokens", return_value=CONTEXT_TOKEN_BUDGET + 1
        ):
            result = provider._build_file_context(parsed, [str(file1)])

        assert result == ""


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
