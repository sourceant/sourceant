from unittest.mock import MagicMock
import requests
from src.utils.diff import (
    get_diff,
    get_diff_from_pr,
    get_diff_from_push,
    get_diff_between_shas,
)
from src.models.repository_event import RepositoryEvent

TEST_REPO = "sourceant/sourceant"


def test_get_diff_no_event(mocker):
    mock_logger = mocker.patch("src.utils.diff.logger")
    assert get_diff(None) is None
    mock_logger.error.assert_called_once_with("Event is None. Cannot compute diff.")


def test_get_diff_no_payload(mocker):
    mock_logger = mocker.patch("src.utils.diff.logger")
    event = RepositoryEvent(payload=None)
    assert get_diff(event) is None
    mock_logger.error.assert_called_once_with("Payload not found in event.")


def test_get_diff_no_diff_to_compute(mocker):
    mock_logger = mocker.patch("src.utils.diff.logger")
    event = RepositoryEvent(payload={"repository": {"full_name": TEST_REPO}})
    assert get_diff(event) is None
    mock_logger.info.assert_called_once_with("No diff to compute for this event.")


def test_get_diff_pr_event(mocker):
    mock_get_diff_from_pr = mocker.patch("src.utils.diff.get_diff_from_pr")
    mock_get = mocker.patch("requests.get")
    payload = {
        "action": "opened",
        "number": 123,
        "repository": {"full_name": TEST_REPO},
    }
    event = RepositoryEvent(payload=payload)
    get_diff(event)
    mock_get_diff_from_pr.assert_called_once_with(TEST_REPO, 123, {})
    mock_get.assert_not_called()


def test_get_diff_push_event(mocker):
    mock_get_diff_from_push = mocker.patch("src.utils.diff.get_diff_from_push")
    mock_get = mocker.patch("requests.get")
    payload = {
        "after": "sha1",
        "base_ref": "refs/heads/main",
        "repository": {"full_name": TEST_REPO},
    }
    event = RepositoryEvent(payload=payload)
    get_diff(event)
    mock_get_diff_from_push.assert_called_once_with(
        TEST_REPO, "refs/heads/main", "sha1", {}
    )
    mock_get.assert_not_called()


def test_get_diff_private_repo(mocker):
    mock_get_diff_from_pr = mocker.patch("src.utils.diff.get_diff_from_pr")
    mock_get = mocker.patch("requests.get")  # mock requests.get
    mocker.patch("src.utils.diff.os.environ.get", return_value="test_token")
    payload = {
        "action": "opened",
        "number": 123,
        "repository": {"private": True, "full_name": TEST_REPO},
    }
    event = RepositoryEvent(payload=payload)
    get_diff(event)
    mock_get_diff_from_pr.assert_called_once_with(
        TEST_REPO, 123, {"Authorization": "token test_token"}
    )
    mock_get.assert_not_called()


def test_get_diff_private_repo_no_token(mocker):
    mock_logger = mocker.patch("src.utils.diff.logger")
    mocker.patch("src.utils.diff.os.environ.get", return_value=None)
    payload = {
        "action": "opened",
        "number": 123,
        "repository": {"full_name": TEST_REPO, "private": True},
    }
    event = RepositoryEvent(payload=payload)
    assert get_diff(event) is None
    mock_logger.error.assert_called_once_with(
        "GITHUB_TOKEN environment variable not set for private repo."
    )


def test_get_diff_from_pr_success(mocker):
    mock_get = mocker.patch("requests.get")
    mock_response_pr = MagicMock()
    mock_response_diff = MagicMock()
    mock_response_pr.status_code = 200
    mock_response_diff.status_code = 200
    mock_response_pr.json.return_value = {"diff_url": "http://example.com/diff"}
    mock_response_diff.text = "This is a mock diff"
    mock_get.side_effect = [mock_response_pr, mock_response_diff]
    result = get_diff_from_pr(TEST_REPO, 123, {})
    assert result == "This is a mock diff"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0] == (
        (f"https://api.github.com/repos/{TEST_REPO}/pulls/123",),
        {"headers": {"Accept": "application/vnd.github.v3.json"}},
    )
    assert mock_get.call_args_list[1] == (
        ("http://example.com/diff",),
        {"headers": {"Accept": "application/vnd.github.v3.diff"}},
    )


def test_get_diff_from_push_success(mocker):
    mock_get = mocker.patch("requests.get")
    mock_response_diff = MagicMock()
    mock_response_diff.status_code = 200
    mock_response_diff.text = "This is a mock push diff"
    mock_get.return_value = mock_response_diff
    result = get_diff_from_push(TEST_REPO, "base_ref", "after_sha", {})
    assert result == "This is a mock push diff"
    mock_get.assert_called_once_with(
        f"https://api.github.com/repos/{TEST_REPO}/compare/base_ref...after_sha",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )


def test_get_diff_from_pr_diff_url_missing(mocker):
    mock_get = mocker.patch("requests.get")
    mock_response_pr = MagicMock()
    mock_response_pr.status_code = 200
    mock_response_pr.json.return_value = {}  # Missing diff_url
    mock_get.return_value = mock_response_pr
    mock_logger = mocker.patch("src.utils.diff.logger")
    result = get_diff_from_pr(TEST_REPO, 123, {})
    assert result is None
    mock_logger.error.assert_called_once_with("Diff URL not found in PR response.")


def test_get_diff_from_pr_http_error(mocker):
    mock_get = mocker.patch("requests.get")
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "HTTP Error"
    )
    mock_get.return_value = mock_response
    mock_logger = mocker.patch("src.utils.diff.logger")
    result = get_diff_from_pr(TEST_REPO, 123, {})
    assert result is None
    mock_logger.error.assert_called_once()


def test_get_diff_from_push_http_error(mocker):
    mock_get = mocker.patch("requests.get")
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "HTTP Error"
    )
    mock_get.return_value = mock_response
    mock_logger = mocker.patch("src.utils.diff.logger")
    result = get_diff_from_push(TEST_REPO, "base", "head", {})
    assert result is None
    mock_logger.error.assert_called_once()


def test_get_diff_between_shas_http_error(mocker):
    mock_get = mocker.patch("requests.get")
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "HTTP Error"
    )
    mock_get.return_value = mock_response
    mock_logger = mocker.patch("src.utils.diff.logger")
    result = get_diff_between_shas(TEST_REPO, "base", "head", {})
    assert result is None
    mock_logger.error.assert_called_once()
