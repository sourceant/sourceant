import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock

from src.plugins.builtin.repo_manager.plugin import RepoManagerPlugin


@pytest.fixture
def plugin():
    return RepoManagerPlugin()


@pytest.fixture
def mock_pr_event_data():
    return {
        "auth_type": "github_app",
        "repository_event": {
            "number": 10,
            "title": "Add user authentication",
        },
        "repository_context": {
            "owner": "test_owner",
            "name": "test_repo",
            "full_name": "test_owner/test_repo",
        },
        "payload": {
            "pull_request": {
                "body": "This PR adds JWT-based authentication.",
            },
        },
    }


@pytest.fixture
def mock_issue_event_data():
    return {
        "auth_type": "github_app",
        "repository_event": {
            "number": 5,
            "title": "Login page is broken",
        },
        "repository_context": {
            "owner": "test_owner",
            "name": "test_repo",
            "full_name": "test_owner/test_repo",
        },
        "payload": {
            "issue": {
                "body": "The login page returns a 500 error.",
            },
        },
    }


class TestHandleEventGuards:
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    def test_skips_non_github_app_events(self, mock_config, mock_github, plugin):
        event_data = {"auth_type": "oauth", "repository_event": {}, "payload": {}}
        result = asyncio.get_event_loop().run_until_complete(
            plugin._handle_event("pull_request.opened", event_data)
        )
        assert result["processed"] is False
        assert "OAuth" in result["reason"]

    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    def test_skips_when_disabled(
        self, mock_config, mock_github, plugin, mock_pr_event_data
    ):
        mock_config.get_value.return_value = None
        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", False
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )
        assert result["processed"] is False
        assert "disabled" in result["reason"]


class TestPRDedup:
    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_finds_duplicates_and_posts_comment(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        mock_config.get_value.side_effect = lambda *a, **kw: None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_pull_requests.return_value = [
            {"number": 10, "title": "Add user authentication", "body": "JWT auth"},
            {"number": 8, "title": "Add user auth", "body": "Add JWT login"},
            {"number": 3, "title": "Fix readme typo", "body": "Typo fix"},
        ]
        mock_github.list_labels.return_value = []
        mock_github.find_comment_with_marker.return_value = None
        mock_github.get_diff.return_value = "some diff"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.generate_text.return_value = "[8]"

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        assert result["processed"] is True
        assert result["results"]["dedup"]["duplicates"] == [8]
        mock_github.post_issue_comment.assert_called_once()

    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_no_duplicates_no_comment(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        mock_config.get_value.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_pull_requests.return_value = [
            {"number": 10, "title": "Add user authentication", "body": "JWT auth"},
            {"number": 3, "title": "Fix readme typo", "body": "Typo fix"},
        ]
        mock_github.list_labels.return_value = []
        mock_github.get_diff.return_value = "some diff"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.generate_text.return_value = "[]"

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        assert result["processed"] is True
        assert result["results"]["dedup"]["duplicates"] == []
        mock_github.post_issue_comment.assert_not_called()

    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_handles_llm_parse_errors_gracefully(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        mock_config.get_value.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_pull_requests.return_value = [
            {"number": 10, "title": "Add auth", "body": ""},
            {"number": 3, "title": "Other PR", "body": ""},
        ]
        mock_github.list_labels.return_value = []
        mock_github.get_diff.return_value = "diff"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        # Return invalid JSON
        mock_llm_instance.generate_text.return_value = "I think PR #3 is a duplicate"

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        # Should still work via regex fallback
        assert result["processed"] is True
        assert 3 in result["results"]["dedup"]["duplicates"]


class TestIssueDedup:
    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_finds_duplicate_issues_and_posts_comment(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_issue_event_data
    ):
        mock_config.get_value.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_issues.return_value = [
            {"number": 5, "title": "Login page is broken", "body": "500 error"},
            {
                "number": 2,
                "title": "Login returns 500",
                "body": "Server error on login",
            },
            {"number": 1, "title": "Add dark mode", "body": "Theme support"},
        ]
        mock_github.list_labels.return_value = []
        mock_github.find_comment_with_marker.return_value = None

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.generate_text.return_value = "[2]"

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("issues.opened", mock_issue_event_data)
            )

        assert result["processed"] is True
        assert result["results"]["dedup"]["duplicates"] == [2]
        mock_github.post_issue_comment.assert_called_once()


class TestAutoLabel:
    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_suggests_valid_labels_and_applies(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        mock_config.get_value.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_pull_requests.return_value = [
            {"number": 10, "title": "Add auth", "body": ""},
            {"number": 3, "title": "Other PR", "body": ""},
        ]
        mock_github.list_labels.return_value = [
            {"name": "enhancement"},
            {"name": "bug"},
            {"name": "documentation"},
        ]
        mock_github.get_diff.return_value = "some diff"
        mock_github.find_comment_with_marker.return_value = None

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        # First call: dedup, second call: auto-label
        mock_llm_instance.generate_text.side_effect = [
            "[]",  # no duplicates
            '["enhancement"]',  # label suggestion
        ]

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        assert result["processed"] is True
        assert result["results"]["auto_label"]["labels"] == ["enhancement"]
        mock_github.add_labels.assert_called_once_with(
            "test_owner", "test_repo", 10, ["enhancement"]
        )

    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_filters_out_invalid_labels(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        mock_config.get_value.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_pull_requests.return_value = [
            {"number": 10, "title": "Add auth", "body": ""},
            {"number": 3, "title": "Other PR", "body": ""},
        ]
        mock_github.list_labels.return_value = [
            {"name": "bug"},
            {"name": "enhancement"},
        ]
        mock_github.get_diff.return_value = "diff"
        mock_github.find_comment_with_marker.return_value = None

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.generate_text.side_effect = [
            "[]",  # no duplicates
            '["bug", "nonexistent-label", "security"]',  # label suggestion with invalid ones
        ]

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        assert result["results"]["auto_label"]["labels"] == ["bug"]
        mock_github.add_labels.assert_called_once_with(
            "test_owner", "test_repo", 10, ["bug"]
        )

    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_skips_when_repo_has_no_labels(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_issue_event_data
    ):
        mock_config.get_value.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_issues.return_value = [
            {"number": 5, "title": "Login broken", "body": ""},
        ]
        mock_github.list_labels.return_value = []
        mock_github.find_comment_with_marker.return_value = None

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.generate_text.return_value = "[]"

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("issues.opened", mock_issue_event_data)
            )

        assert result["results"]["auto_label"]["status"] == "no_labels_in_repo"
        mock_github.add_labels.assert_not_called()


class TestDedupAndLabelTogether:
    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_both_dedup_and_label_run_for_single_event(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        mock_config.get_value.return_value = None

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        mock_github.list_open_pull_requests.return_value = [
            {"number": 10, "title": "Add auth", "body": ""},
            {"number": 7, "title": "Add authentication", "body": "Auth feature"},
        ]
        mock_github.list_labels.return_value = [
            {"name": "enhancement"},
            {"name": "bug"},
        ]
        mock_github.get_diff.return_value = "diff content"
        mock_github.find_comment_with_marker.return_value = None

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.generate_text.side_effect = [
            "[7]",  # dedup finds PR #7
            '["enhancement"]',  # auto-label
        ]

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", True
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        assert result["processed"] is True
        assert result["results"]["dedup"]["duplicates"] == [7]
        assert result["results"]["auto_label"]["labels"] == ["enhancement"]
        mock_github.post_issue_comment.assert_called_once()
        mock_github.add_labels.assert_called_once()


class TestConfigResolution:
    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_repo_config_overrides_env_default(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        """Repo with repo_manager.enabled=true overrides env default (off)."""

        # Entity config returns True for enabled
        def config_side_effect(configurable_type, configurable_id, key):
            if key == "repo_manager.enabled":
                return True
            return None

        mock_config.get_value.side_effect = config_side_effect

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github
        mock_github.list_open_pull_requests.return_value = [
            {"number": 10, "title": "Test", "body": ""},
        ]
        mock_github.list_labels.return_value = []
        mock_github.get_diff.return_value = "diff"

        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        mock_llm_instance.generate_text.return_value = "[]"

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", False
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        # Should process because entity config says enabled=True
        assert result["processed"] is True

    @patch("src.plugins.builtin.repo_manager.plugin.Config")
    @patch("src.plugins.builtin.repo_manager.plugin.llm")
    @patch("src.plugins.builtin.repo_manager.plugin.GitHub")
    def test_repo_config_disables_specific_features(
        self, mock_github_cls, mock_llm, mock_config, plugin, mock_pr_event_data
    ):
        """Repo with specific features disabled skips those features."""

        def config_side_effect(configurable_type, configurable_id, key):
            if key == "repo_manager.enabled":
                return True
            if key == "repo_manager.pr_triage_enabled":
                return False
            if key == "repo_manager.auto_label_enabled":
                return False
            return None

        mock_config.get_value.side_effect = config_side_effect

        mock_github = MagicMock()
        mock_github_cls.return_value = mock_github

        with patch(
            "src.plugins.builtin.repo_manager.plugin.REPO_MANAGER_ENABLED", False
        ):
            result = asyncio.get_event_loop().run_until_complete(
                plugin._handle_event("pull_request.opened", mock_pr_event_data)
            )

        assert result["processed"] is True
        # Neither dedup nor auto-label should have run
        assert "dedup" not in result["results"]
        assert "auto_label" not in result["results"]
        mock_github.list_open_pull_requests.assert_not_called()
        mock_github.list_labels.assert_not_called()


class TestParseHelpers:
    def test_parse_dedup_response_valid_json(self, plugin):
        assert plugin._parse_dedup_response("[1, 2, 3]") == [1, 2, 3]

    def test_parse_dedup_response_empty(self, plugin):
        assert plugin._parse_dedup_response("[]") == []

    def test_parse_dedup_response_invalid_json_fallback(self, plugin):
        result = plugin._parse_dedup_response("Possible duplicates: #5, #12")
        assert 5 in result
        assert 12 in result

    def test_parse_dedup_response_garbage(self, plugin):
        assert plugin._parse_dedup_response("no numbers here") == []

    def test_parse_label_response_valid(self, plugin):
        repo_labels = ["bug", "enhancement", "documentation"]
        result = plugin._parse_label_response('["bug", "enhancement"]', repo_labels)
        assert result == ["bug", "enhancement"]

    def test_parse_label_response_case_insensitive(self, plugin):
        repo_labels = ["Bug", "Enhancement"]
        result = plugin._parse_label_response('["bug", "ENHANCEMENT"]', repo_labels)
        assert result == ["Bug", "Enhancement"]

    def test_parse_label_response_filters_invalid(self, plugin):
        repo_labels = ["bug", "enhancement"]
        result = plugin._parse_label_response(
            '["bug", "nonexistent", "security"]', repo_labels
        )
        assert result == ["bug"]

    def test_parse_label_response_invalid_json(self, plugin):
        repo_labels = ["bug"]
        result = plugin._parse_label_response("not json", repo_labels)
        assert result == []
