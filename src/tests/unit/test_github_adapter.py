import pytest
import requests
from unittest.mock import patch, mock_open, MagicMock
import time
import os
import unittest
from src.integrations.github.github import GitHub
from src.models.code_review import CodeReview, Verdict, CodeSuggestion, Side
from src.models.repository import Repository
from src.models.pull_request import PullRequest


@pytest.fixture
def github_instance():
    with patch.dict(
        os.environ,
        {
            "GITHUB_APP_ID": "123",
            "GITHUB_APP_PRIVATE_KEY_PATH": "/path/to/key",
            "GITHUB_APP_CLIENT_ID": "456",
        },
    ):
        # Clear the cache for each test
        github = GitHub()
        github._access_tokens = {}
        yield github


@pytest.fixture
def repository_instance():
    return Repository(owner="test_owner", name="test_repo")


@pytest.fixture
def pull_request_instance():
    return PullRequest(number=1, head_sha="abc123")


@pytest.fixture
def code_review_instance():
    return CodeReview(
        summary="Test review summary",
        verdict=Verdict.APPROVE,
        code_suggestions=[
            CodeSuggestion(
                file_name="test.py",
                position=1,
                line=10,
                start_line=10,
                side=Side.RIGHT,
                comment="This is a test suggestion.",
                category="style",
                suggested_code="print('hello world')",
            )
        ],
    )


@pytest.fixture
def code_review_instance_no_summary():
    return CodeReview(
        summary=None,
        verdict=Verdict.COMMENT,
        code_suggestions=[
            CodeSuggestion(
                file_name="test.py",
                position=1,
                line=10,
                start_line=10,
                side=Side.RIGHT,
                comment="This is a test suggestion.",
                category="style",
                suggested_code="print('hello world')",
            )
        ],
    )


@pytest.fixture
def code_review_instance_no_suggestions():
    return CodeReview(
        summary="Test review summary",
        verdict=Verdict.APPROVE,
        code_suggestions=[],
    )


@pytest.fixture
def code_review_instance_comment_no_suggestions():
    return CodeReview(
        summary="Test review summary",
        verdict=Verdict.COMMENT,
        code_suggestions=[],
    )


def test_generate_jwt(github_instance):
    with patch(
        "builtins.open", new_callable=mock_open, read_data="test_private_key"
    ), patch("jwt.encode") as mock_jwt_encode:
        github_instance.generate_jwt()
        mock_jwt_encode.assert_called_once()


def test_get_installation_id(github_instance, repository_instance):
    with patch(
        "src.integrations.github.github.GitHub.generate_jwt", return_value="test_jwt"
    ), patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 12345}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        installation_id = github_instance.get_installation_id(
            repository_instance.owner, repository_instance.name
        )
        assert installation_id == 12345
        mock_get.assert_called_once()


def test_get_installation_access_token_caching(github_instance, repository_instance):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_id", return_value=12345
    ), patch(
        "src.integrations.github.github.GitHub.generate_jwt", return_value="test_jwt"
    ), patch(
        "requests.post"
    ) as mock_post:

        mock_response = MagicMock()
        # Corrected to match the new implementation that uses time.time()
        mock_response.json.return_value = {
            "token": "test_access_token",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        # First call, should call the API
        token1 = github_instance.get_installation_access_token(
            repository_instance.owner, repository_instance.name
        )
        assert token1 == "test_access_token"
        mock_post.assert_called_once()

        # Second call, should use cache
        token2 = github_instance.get_installation_access_token(
            repository_instance.owner, repository_instance.name
        )
        assert token2 == "test_access_token"
        mock_post.assert_called_once()  # Should not be called again

        # Force token to expire
        repo_full_name = f"{repository_instance.owner}/{repository_instance.name}"
        github_instance._access_tokens[repo_full_name]["expires_at"] = time.time() - 1

        # Third call, should call API again
        token3 = github_instance.get_installation_access_token(
            repository_instance.owner, repository_instance.name
        )
        assert token3 == "test_access_token"
        assert mock_post.call_count == 2


def test_get_app_slug(github_instance):
    with patch(
        "src.integrations.github.github.GitHub.generate_jwt", return_value="test_jwt"
    ), patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {"slug": "test-app"}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        app_slug = github_instance.get_app_slug()
        assert app_slug == "test-app"
        mock_get.assert_called_once_with(
            "https://api.github.com/app", headers=unittest.mock.ANY, timeout=30
        )


def test_find_overview_comment_found(
    github_instance, repository_instance, pull_request_instance
):
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"id": 123, "body": "Some comment"},
            {"id": 456, "body": "Review summary <!-- SOURCEANT_REVIEW_SUMMARY -->"},
        ]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        comment = github_instance._find_overview_comment(
            repository_instance.owner,
            repository_instance.name,
            pull_request_instance.number,
            {},
        )
        assert comment["id"] == 456
        mock_get.assert_called_once()


def test_find_overview_comment_not_found(
    github_instance, repository_instance, pull_request_instance
):
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": 123, "body": "Some other comment"}]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        comment_id = github_instance._find_overview_comment(
            repository_instance.owner,
            repository_instance.name,
            pull_request_instance.number,
            {},
        )
        assert comment_id is None


def test_create_or_update_overview_comment_create(
    github_instance, repository_instance, pull_request_instance
):
    with patch(
        "src.integrations.github.github.GitHub._find_overview_comment",
        return_value=None,
    ), patch("requests.post") as mock_post:

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        github_instance._create_or_update_overview_comment(
            repository_instance.owner,
            repository_instance.name,
            pull_request_instance.number,
            "New summary",
            {},
        )
        mock_post.assert_called_once()
        assert (
            f"/issues/{pull_request_instance.number}/comments"
            in mock_post.call_args[0][0]
        )


def test_create_or_update_overview_comment_update(
    github_instance, repository_instance, pull_request_instance
):
    with patch(
        "src.integrations.github.github.GitHub._find_overview_comment",
        return_value={"id": 123, "body": "old summary"},
    ), patch("requests.patch") as mock_patch:

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_patch.return_value = mock_response

        github_instance._create_or_update_overview_comment(
            repository_instance.owner,
            repository_instance.name,
            pull_request_instance.number,
            "Updated summary",
            {},
        )
        mock_patch.assert_called_once()
        assert "/issues/comments/123" in mock_patch.call_args[0][0]


def test_has_existing_bot_approval_true(github_instance):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_token",
    ), patch(
        "src.integrations.github.github.GitHub.get_app_slug",
        return_value="sourceant",
    ), patch(
        "requests.get"
    ) as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "user": {"login": "sourceant[bot]"},
                "state": "APPROVED",
            }
        ]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        assert github_instance.has_existing_bot_approval("owner", "repo", 1) is True


def test_has_existing_bot_approval_false(github_instance):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_token",
    ), patch(
        "src.integrations.github.github.GitHub.get_app_slug",
        return_value="sourceant",
    ), patch(
        "requests.get"
    ) as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "user": {"login": "other-user"},
                "state": "APPROVED",
            },
            {
                "user": {"login": "sourceant[bot]"},
                "state": "COMMENTED",
            },
        ]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        assert github_instance.has_existing_bot_approval("owner", "repo", 1) is False


def test_has_existing_bot_approval_paginates(github_instance):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_token",
    ), patch(
        "src.integrations.github.github.GitHub.get_app_slug",
        return_value="sourceant",
    ), patch(
        "requests.get"
    ) as mock_get:
        first_page = MagicMock()
        first_page.json.return_value = [
            {"user": {"login": "other-user"}, "state": "APPROVED"}
        ] * 100
        first_page.raise_for_status.return_value = None
        second_page = MagicMock()
        second_page.json.return_value = [
            {"user": {"login": "sourceant[bot]"}, "state": "APPROVED"}
        ]
        second_page.raise_for_status.return_value = None
        mock_get.side_effect = [first_page, second_page]

        assert github_instance.has_existing_bot_approval("owner", "repo", 1) is True
        assert mock_get.call_count == 2


def test_has_existing_bot_approval_false_after_request_changes(github_instance):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_token",
    ), patch(
        "src.integrations.github.github.GitHub.get_app_slug",
        return_value="sourceant",
    ), patch(
        "requests.get"
    ) as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "user": {"login": "sourceant[bot]"},
                "state": "APPROVED",
            },
            {
                "user": {"login": "sourceant[bot]"},
                "state": "CHANGES_REQUESTED",
            },
        ]
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        assert github_instance.has_existing_bot_approval("owner", "repo", 1) is False


def test_post_review_uses_line_side_and_commit_id(
    github_instance, repository_instance, pull_request_instance
):
    """Review comments use line/side and payload includes commit_id."""
    from src.utils.line_mapper import LineMapper

    from src.models.code_review import SuggestionCategory

    review = CodeReview(
        summary=None,
        verdict=Verdict.COMMENT,
        code_suggestions=[
            CodeSuggestion(
                file_name="test.py",
                position=5,
                start_line=10,
                end_line=10,
                side=Side.RIGHT,
                comment="Fix this.",
                category=SuggestionCategory.STYLE,
                suggested_code="fixed()",
            )
        ],
    )

    mock_mapper = MagicMock(spec=LineMapper)

    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_token",
    ), patch(
        "src.integrations.github.github.GitHub._find_overview_comment",
        return_value=None,
    ), patch(
        "requests.post"
    ) as mock_post, patch(
        "requests.get"
    ):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        github_instance.post_review(
            repository=repository_instance,
            pull_request=pull_request_instance,
            code_review=review,
            line_mapper=mock_mapper,
        )

        review_call = None
        for call in mock_post.call_args_list:
            if "/reviews" in call[0][0]:
                review_call = call
                break

        assert review_call is not None
        payload = review_call[1]["json"]
        assert payload["commit_id"] == "abc123"
        comment = payload["comments"][0]
        assert "line" in comment
        assert "side" in comment
        assert comment["line"] == 10
        assert comment["side"] == "RIGHT"


class TestPostReviewRetryOn422:
    def test_retries_on_422_removing_invalid_comment(self, github_instance):
        error_response = MagicMock()
        error_response.status_code = 422
        error_response.json.return_value = {
            "message": "Validation Failed",
            "errors": [{"field": "comments[1].line", "code": "invalid"}],
        }

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {"id": 42}
        success_response.raise_for_status.return_value = None

        with patch("requests.post", side_effect=[error_response, success_response]):
            result = github_instance._post_review_with_retry(
                "owner",
                "repo",
                1,
                {
                    "body": "Review",
                    "event": "COMMENT",
                    "comments": [
                        {"path": "a.py", "line": 1, "body": "ok"},
                        {"path": "b.py", "line": 99, "body": "bad"},
                        {"path": "c.py", "line": 5, "body": "fine"},
                    ],
                },
                {"Authorization": "Bearer test"},
            )

        assert result["id"] == 42

    def test_identify_invalid_comments_parses_field(self, github_instance):
        error_body = {
            "errors": [
                {"field": "comments[0].line", "code": "invalid"},
                {"field": "comments[2].position", "code": "invalid"},
            ]
        }
        result = github_instance._identify_invalid_comments(error_body, [])
        assert result == [0, 2]

    def test_identify_invalid_comments_parses_message(self, github_instance):
        error_body = {
            "errors": [
                {"field": "comments", "message": "comments[1] is invalid"},
            ]
        }
        result = github_instance._identify_invalid_comments(error_body, [])
        assert result == [1]

    def test_raises_when_no_invalid_indices_found(self, github_instance):
        error_response = MagicMock()
        error_response.status_code = 422
        error_response.json.return_value = {
            "message": "Validation Failed",
            "errors": [{"field": "body", "code": "invalid"}],
        }
        error_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=error_response
        )

        with patch("requests.post", return_value=error_response):
            with pytest.raises(requests.exceptions.HTTPError):
                github_instance._post_review_with_retry(
                    "owner",
                    "repo",
                    1,
                    {"body": "Review", "event": "COMMENT", "comments": []},
                    {"Authorization": "Bearer test"},
                )


class TestGetExistingBotReviewComments:
    def test_returns_bot_comments(self, github_instance):
        with patch(
            "src.integrations.github.github.GitHub.get_installation_access_token",
            return_value="test_token",
        ), patch(
            "src.integrations.github.github.GitHub.get_app_slug",
            return_value="sourceant",
        ), patch(
            "requests.get"
        ) as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {
                    "user": {"login": "sourceant[bot]"},
                    "path": "src/main.py",
                    "body": "Consider using a context manager here.",
                    "line": 42,
                    "start_line": 40,
                },
                {
                    "user": {"login": "human-reviewer"},
                    "path": "src/main.py",
                    "body": "LGTM",
                    "line": 10,
                    "start_line": None,
                },
                {
                    "user": {"login": "sourceant[bot]"},
                    "path": "src/utils.py",
                    "body": "Unused import.",
                    "line": 3,
                    "start_line": None,
                },
            ]
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            result = github_instance.get_existing_bot_review_comments(
                "owner", "repo", 1
            )

            assert len(result) == 2
            assert result[0]["path"] == "src/main.py"
            assert result[0]["line"] == 42
            assert result[0]["start_line"] == 40
            assert result[1]["path"] == "src/utils.py"

    def test_returns_empty_on_no_bot_comments(self, github_instance):
        with patch(
            "src.integrations.github.github.GitHub.get_installation_access_token",
            return_value="test_token",
        ), patch(
            "src.integrations.github.github.GitHub.get_app_slug",
            return_value="sourceant",
        ), patch(
            "requests.get"
        ) as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {"user": {"login": "human"}, "path": "a.py", "body": "ok", "line": 1}
            ]
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            result = github_instance.get_existing_bot_review_comments(
                "owner", "repo", 1
            )
            assert result == []

    def test_returns_empty_on_error(self, github_instance):
        with patch(
            "src.integrations.github.github.GitHub.get_installation_access_token",
            side_effect=Exception("auth failed"),
        ):
            result = github_instance.get_existing_bot_review_comments(
                "owner", "repo", 1
            )
            assert result == []

    def test_paginates(self, github_instance):
        with patch(
            "src.integrations.github.github.GitHub.get_installation_access_token",
            return_value="test_token",
        ), patch(
            "src.integrations.github.github.GitHub.get_app_slug",
            return_value="sourceant",
        ), patch(
            "requests.get"
        ) as mock_get:
            page1 = MagicMock()
            page1.json.return_value = [
                {
                    "user": {"login": "other"},
                    "path": "a.py",
                    "body": "x",
                    "line": 1,
                    "start_line": None,
                }
            ] * 100
            page1.raise_for_status.return_value = None

            page2 = MagicMock()
            page2.json.return_value = [
                {
                    "user": {"login": "sourceant[bot]"},
                    "path": "b.py",
                    "body": "fix this",
                    "line": 5,
                    "start_line": None,
                }
            ]
            page2.raise_for_status.return_value = None

            mock_get.side_effect = [page1, page2]

            result = github_instance.get_existing_bot_review_comments(
                "owner", "repo", 1
            )
            assert len(result) == 1
            assert result[0]["path"] == "b.py"
            assert mock_get.call_count == 2


def test_get_diff(github_instance, repository_instance, pull_request_instance):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_access_token",
    ), patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.text = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1,1 +1,1 @@\n-hello\n+world"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        diff = github_instance.get_diff(
            repository_instance.owner,
            repository_instance.name,
            pull_request_instance.number,
        )

        assert diff == mock_response.text
        mock_get.assert_called_once()
