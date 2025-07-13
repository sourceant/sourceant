import pytest
from unittest.mock import patch, MagicMock

from src.events.dispatcher import EventDispatcher
from src.events.repository_event import RepositoryEvent
from src.models.repository_event import (
    RepositoryEvent as RepositoryEventModel,
)


class TestProcessEvent:
    @pytest.fixture
    def dispatcher(self):
        """Fixture to create an EventDispatcher instance."""
        return EventDispatcher()

    @patch("src.events.dispatcher.logger")
    @patch("src.events.dispatcher.GitHub")
    def test_skips_merged_pr(self, mock_github, mock_logger, dispatcher):
        """Test that merged pull requests are skipped."""
        event_data = RepositoryEventModel(
            provider="github",
            type="pull_request",
            action="opened",
            repository_full_name="test/repo",
            number=1,
            title="Test PR",
            payload={"pull_request": {"merged": True, "draft": False}},
        )
        event = RepositoryEvent(event_data)

        dispatcher._process_event(event)

        mock_logger.info.assert_any_call(
            f"Pull request #{event.data.number} is already merged. Skipping."
        )
        mock_github.assert_not_called()

    @patch("src.events.dispatcher.logger")
    @patch("src.events.dispatcher.GitHub")
    def test_skips_draft_pr_when_disabled(
        self, mock_github, mock_logger, dispatcher, monkeypatch
    ):
        """Test that draft PRs are skipped when REVIEW_DRAFT_PRS is False."""
        monkeypatch.setattr("src.events.dispatcher.REVIEW_DRAFT_PRS", False)
        event_data = RepositoryEventModel(
            provider="github",
            type="pull_request",
            action="opened",
            repository_full_name="test/repo",
            number=2,
            title="Draft PR",
            payload={"pull_request": {"merged": False, "draft": True}},
        )
        event = RepositoryEvent(event_data)

        dispatcher._process_event(event)

        mock_logger.info.assert_any_call(
            f"Pull request #{event.data.number} is a draft. Skipping review."
        )
        mock_github.assert_not_called()

    @patch("src.events.dispatcher.logger")
    @patch("src.events.dispatcher.GitHub")
    def test_processes_draft_pr_when_enabled(
        self, mock_github, mock_logger, dispatcher, monkeypatch
    ):
        """Test that draft PRs are processed when REVIEW_DRAFT_PRS is True."""
        monkeypatch.setattr("src.events.dispatcher.REVIEW_DRAFT_PRS", True)
        mock_github_instance = MagicMock()
        mock_github.return_value = mock_github_instance

        event_data = RepositoryEventModel(
            provider="github",
            type="pull_request",
            action="opened",
            repository_full_name="test/repo",
            number=3,
            title="Draft PR",
            payload={"pull_request": {"merged": False, "draft": True}},
        )
        event = RepositoryEvent(event_data)

        dispatcher._process_event(event)

        assert not any(
            "Skipping review" in call.args[0]
            for call in mock_logger.info.call_args_list
        )
        mock_github_instance.get_diff.assert_called_once()

    @patch("src.events.dispatcher.logger")
    @patch("src.events.dispatcher.GitHub")
    def test_processes_normal_pr(self, mock_github, mock_logger, dispatcher):
        """Test that normal (non-draft, non-merged) PRs are processed."""
        mock_github_instance = MagicMock()
        mock_github.return_value = mock_github_instance

        event_data = RepositoryEventModel(
            provider="github",
            type="pull_request",
            action="opened",
            repository_full_name="test/repo",
            number=4,
            title="Normal PR",
            payload={"pull_request": {"merged": False, "draft": False}},
        )
        event = RepositoryEvent(event_data)

        dispatcher._process_event(event)

        assert not any(
            "Skipping review" in call.args[0]
            for call in mock_logger.info.call_args_list
        )
        mock_github_instance.get_diff.assert_called_once()
