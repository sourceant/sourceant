import pytest
from unittest.mock import patch, mock_open, MagicMock
import jwt
import time
import requests
import os
from src.integrations.github.github import GitHub
from src.models.code_review import CodeReview, Verdict, CodeSuggestion
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
        yield GitHub()


@pytest.fixture
def repository_instance():
    return Repository(owner="test_owner", name="test_repo")


@pytest.fixture
def pull_request_instance():
    return PullRequest(number=1)


@pytest.fixture
def code_review_instance():
    return CodeReview(
        summary="Test review",
        verdict=Verdict.APPROVE,
        code_suggestions=[
            CodeSuggestion(
                file_name="test.py",
                position=1,
                line=1,
                start_line=1,
                side="RIGHT",
                comment="Test suggestion",
                category="style",
                suggested_code="print('test')",
            )
        ],
    )


def test_init_raises_value_error_if_env_vars_not_set():
    with patch.dict(os.environ, {}), pytest.raises(ValueError):
        GitHub()


def test_generate_jwt(github_instance):
    with patch(
        "builtins.open", new_callable=mock_open, read_data="test_private_key"
    ) as mock_file_open, patch("jwt.encode") as mock_jwt_encode:
        github_instance.generate_jwt()
        mock_file_open.assert_called_once_with("/path/to/key", "r")
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


def test_get_installation_access_token(github_instance, repository_instance):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_id", return_value=12345
    ), patch(
        "src.integrations.github.github.GitHub.generate_jwt", return_value="test_jwt"
    ), patch(
        "requests.post"
    ) as mock_post:
        mock_response = MagicMock()
        mock_response.json.return_value = {"token": "test_access_token"}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        token = github_instance.get_installation_access_token(
            repository_instance.owner, repository_instance.name
        )
        assert token == "test_access_token"
        mock_post.assert_called_once()


def test_post_review_success(
    github_instance, repository_instance, pull_request_instance, code_review_instance
):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_access_token",
    ), patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = github_instance.post_review(
            repository_instance, pull_request_instance, code_review_instance
        )
        assert result["status"] == "success"
        mock_post.assert_called_once()


def test_post_review_request_exception(
    github_instance, repository_instance, pull_request_instance, code_review_instance
):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_access_token",
    ), patch(
        "requests.post",
        side_effect=requests.exceptions.RequestException("Test exception"),
    ):
        result = github_instance.post_review(
            repository_instance, pull_request_instance, code_review_instance
        )
        assert result["status"] == "error"


def test_post_review_generic_exception(
    github_instance, repository_instance, pull_request_instance, code_review_instance
):
    with patch(
        "src.integrations.github.github.GitHub.get_installation_access_token",
        return_value="test_access_token",
    ), patch("requests.post", side_effect=Exception("Generic exception")):
        result = github_instance.post_review(
            repository_instance, pull_request_instance, code_review_instance
        )
        assert result["status"] == "error"
