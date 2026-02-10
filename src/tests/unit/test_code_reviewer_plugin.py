import pytest
from unittest.mock import patch, MagicMock

from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin
from src.models.code_review import (
    CodeReview,
    CodeSuggestion,
    Side,
    SuggestionCategory,
    Verdict,
)
from src.models.repository import Repository
from src.models.pull_request import PullRequest


@pytest.fixture
def plugin():
    return CodeReviewerPlugin()


@pytest.fixture
def repository():
    return Repository(owner="test_owner", name="test_repo")


@pytest.fixture
def pull_request():
    pr = MagicMock(spec=PullRequest)
    pr.number = 1
    pr.title = "Test PR"
    pr.draft = False
    pr.merged = False
    pr.base_sha = "base_sha_abc"
    pr.head_sha = "head_sha_def"
    return pr


class TestIncrementalReview:
    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.llm")
    def test_synchronize_uses_incremental_diff(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = "prev_sha_123"

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff_between_shas.return_value = "incremental diff"
        mock_github.get_diff.return_value = "full diff"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.uploads_enabled = False

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}
        mock_github.get_file_content.return_value = None

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            plugin._generate_and_post_review(
                repository,
                pull_request,
                event_type="pull_request.synchronize",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_github.get_diff_between_shas.assert_called_once_with(
            owner="test_owner",
            repo="test_repo",
            base_sha="prev_sha_123",
            head_sha="head_sha_def",
        )
        mock_github.get_diff.assert_not_called()
        assert result["status"] == "success"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.llm")
    def test_synchronize_falls_back_on_force_push(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = "old_sha_gone"

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff_between_shas.side_effect = ValueError("Not found")
        mock_github.get_diff.return_value = "full diff content"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.uploads_enabled = False

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}
        mock_github.get_file_content.return_value = None

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            plugin._generate_and_post_review(
                repository,
                pull_request,
                event_type="pull_request.synchronize",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_github.get_diff.assert_called_once()
        assert result["status"] == "success"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.llm")
    def test_first_review_uses_full_diff(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = "full diff"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.uploads_enabled = False

        review = CodeReview(
            verdict=Verdict.COMMENT,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}
        mock_github.get_file_content.return_value = None

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            plugin._generate_and_post_review(
                repository,
                pull_request,
                event_type="pull_request.synchronize",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_github.get_diff_between_shas.assert_not_called()
        mock_github.get_diff.assert_called_once()
        assert result["status"] == "success"

    @patch("src.plugins.builtin.code_reviewer.plugin.save_review_record")
    @patch("src.plugins.builtin.code_reviewer.plugin.get_last_reviewed_sha")
    @patch("src.plugins.builtin.code_reviewer.plugin.GitHub")
    @patch("src.plugins.builtin.code_reviewer.plugin.llm")
    def test_saves_review_record_on_success(
        self,
        mock_llm,
        mock_github_cls,
        mock_get_sha,
        mock_save_record,
        plugin,
        repository,
        pull_request,
    ):
        mock_get_sha.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.get_diff.return_value = "full diff"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.count_tokens.return_value = 100
        mock_llm_instance.token_limit = 1000000
        mock_llm_instance.uploads_enabled = False

        review = CodeReview(
            verdict=Verdict.APPROVE,
            code_suggestions=[],
        )
        mock_llm_instance.generate_code_review.return_value = review
        mock_github.post_review.return_value = {"status": "success"}
        mock_github.get_file_content.return_value = None

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            plugin._generate_and_post_review(
                repository,
                pull_request,
                event_type="pull_request.opened",
                repository_full_name="test_owner/test_repo",
            )
        )

        mock_save_record.assert_called_once_with(
            "test_owner/test_repo",
            pull_request.number,
            pull_request.head_sha,
            pull_request.base_sha,
        )


def _make_suggestion(
    file_name="test.py",
    start_line=10,
    end_line=12,
    comment="Consider using a context manager here for resource cleanup.",
    suggested_code="with open(path) as f:\n    data = f.read()",
):
    return CodeSuggestion(
        file_name=file_name,
        start_line=start_line,
        end_line=end_line,
        side=Side.RIGHT,
        comment=comment,
        category=SuggestionCategory.STYLE,
        suggested_code=suggested_code,
    )


class TestFilterDuplicateSuggestions:
    def test_removes_duplicate_same_file_overlapping_lines_similar_comment(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_removes_duplicate_by_matching_suggested_code(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Rephrased differently.\n\n```suggestion\nwith open(path) as f:\n    data = f.read()\n```",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_removes_duplicate_with_fuzzy_line_shift(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 15,
                "start_line": 13,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_keeps_suggestion_on_different_file(self, plugin):
        existing = [
            {
                "path": "other.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 1

    def test_keeps_suggestion_on_non_overlapping_lines(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 50,
                "start_line": 48,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 1

    def test_keeps_suggestion_with_different_comment_and_code(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Variable naming is inconsistent with project conventions.\n\n```suggestion\nuser_name = get_name()\n```",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 1

    def test_filters_when_existing_body_contains_suggestion_block(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Use context manager.\n\n```suggestion\nwith open(path) as f:\n    data = f.read()\n```",
                "line": 11,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_handles_empty_existing_comments(self, plugin):
        suggestions = [_make_suggestion()]
        result = plugin._filter_duplicate_suggestions(suggestions, [])
        assert len(result) == 1

    def test_handles_single_line_existing_comment(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Consider using a context manager here for resource cleanup.",
                "line": 11,
                "start_line": None,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_detects_rephrased_duplicate_via_high_word_overlap(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "You should consider using a context manager here to ensure proper resource cleanup.",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0

    def test_detects_duplicate_with_similar_code_different_whitespace(self, plugin):
        existing = [
            {
                "path": "test.py",
                "body": "Fix.\n\n```suggestion\nwith  open( path )  as  f:\n    data  =  f.read()\n```",
                "line": 12,
                "start_line": 10,
            },
        ]
        suggestions = [_make_suggestion()]

        result = plugin._filter_duplicate_suggestions(suggestions, existing)
        assert len(result) == 0
