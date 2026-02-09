import pytest
from unittest.mock import patch, MagicMock

from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin
from src.models.code_review import CodeReview, Verdict
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
