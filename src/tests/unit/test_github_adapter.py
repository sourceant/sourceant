import pytest
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
    return PullRequest(number=1)


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


def test_init_raises_value_error_if_env_vars_not_set():
    with patch.dict(os.environ, {}), pytest.raises(ValueError):
        GitHub()


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
        mock_response.json.return_value = {"token": "test_access_token"}
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


def test_find_and_dismiss_previous_reviews(
    github_instance, repository_instance, pull_request_instance
):
    with patch(
        "src.integrations.github.github.GitHub.get_app_slug", return_value="test-app"
    ), patch("requests.get") as mock_get, patch("requests.put") as mock_put:

        mock_get_response = MagicMock()
        mock_get_response.json.return_value = [
            {"id": 1, "user": {"login": "test-app[bot]"}, "state": "COMMENTED"},
            {"id": 2, "user": {"login": "another-user"}, "state": "COMMENTED"},
            {"id": 3, "user": {"login": "test-app[bot]"}, "state": "DISMISSED"},
        ]
        mock_get_response.raise_for_status.return_value = None
        mock_get.return_value = mock_get_response

        mock_put_response = MagicMock()
        mock_put_response.raise_for_status.return_value = None
        mock_put.return_value = mock_put_response

        headers = {"Authorization": "Bearer test_token"}
        github_instance._find_and_dismiss_previous_reviews(
            repository_instance.owner,
            repository_instance.name,
            pull_request_instance.number,
            headers,
        )

        mock_put.assert_called_once()
        dismiss_url = mock_put.call_args[0][0]
        assert "/reviews/1/dismissals" in dismiss_url


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

        comment_id = github_instance._find_overview_comment(
            repository_instance.owner,
            repository_instance.name,
            pull_request_instance.number,
            {},
        )
        assert comment_id == 456
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
        "src.integrations.github.github.GitHub._find_overview_comment", return_value=123
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


@patch("src.integrations.github.github.GitHub._create_or_update_overview_comment")
@patch("src.integrations.github.github.GitHub._find_and_dismiss_previous_reviews")
@patch("requests.post")
@patch(
    "src.integrations.github.github.GitHub.get_installation_access_token",
    return_value="test_token",
)
def test_post_review_full_flow(
    mock_get_token,
    mock_post_review,
    mock_dismiss,
    mock_overview,
    github_instance,
    repository_instance,
    pull_request_instance,
    code_review_instance,
):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {"id": 1, "status": "success"}
    mock_post_review.return_value = mock_response

    result = github_instance._post_review(
        repository_instance, pull_request_instance, code_review_instance
    )

    assert result["status"] == "success"
    mock_get_token.assert_called_once()
    mock_overview.assert_called_once_with(
        repository_instance.owner,
        repository_instance.name,
        pull_request_instance.number,
        code_review_instance.summary,
        unittest.mock.ANY,
    )
    mock_dismiss.assert_called_once()
    mock_post_review.assert_called_once()

    payload = mock_post_review.call_args.kwargs["json"]
    assert payload["event"] == Verdict.APPROVE.value
    assert payload["body"] == "Review complete. See the overview comment for a summary."
    assert len(payload["comments"]) == 1


@patch("src.integrations.github.github.GitHub._create_or_update_overview_comment")
@patch("src.integrations.github.github.GitHub._find_and_dismiss_previous_reviews")
@patch("requests.post")
@patch(
    "src.integrations.github.github.GitHub.get_installation_access_token",
    return_value="test_token",
)
def test_post_review_no_summary(
    mock_get_token,
    mock_post_review,
    mock_dismiss,
    mock_overview,
    github_instance,
    repository_instance,
    pull_request_instance,
    code_review_instance_no_summary,
):
    result = github_instance._post_review(
        repository_instance, pull_request_instance, code_review_instance_no_summary
    )

    assert result["status"] == "success"
    mock_overview.assert_not_called()
    mock_dismiss.assert_called_once()
    mock_post_review.assert_called_once()


@patch("src.integrations.github.github.GitHub._create_or_update_overview_comment")
@patch("src.integrations.github.github.GitHub._find_and_dismiss_previous_reviews")
@patch("requests.post")
@patch(
    "src.integrations.github.github.GitHub.get_installation_access_token",
    return_value="test_token",
)
def test_post_review_skip_formal_review(
    mock_get_token,
    mock_post_review,
    mock_dismiss,
    mock_overview,
    github_instance,
    repository_instance,
    pull_request_instance,
    code_review_instance_comment_no_suggestions,
):
    result = github_instance._post_review(
        repository_instance,
        pull_request_instance,
        code_review_instance_comment_no_suggestions,
    )

    assert result["status"] == "success"
    mock_overview.assert_called_once()
    mock_dismiss.assert_called_once()
    mock_post_review.assert_not_called()


@patch(
    "src.integrations.github.github.GitHub.get_installation_access_token",
    side_effect=ValueError("Token error"),
)
def test_post_review_token_error(
    mock_get_token,
    github_instance,
    repository_instance,
    pull_request_instance,
    code_review_instance,
):
    result = github_instance._post_review(
        repository_instance, pull_request_instance, code_review_instance
    )
    assert result["status"] == "error"
    assert "Token error" in result["message"]
    mock_get_token.assert_called_once()
