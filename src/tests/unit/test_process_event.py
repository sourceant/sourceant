import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.events.dispatcher import EventDispatcher
from src.events.repository_event import RepositoryEvent
from src.models.repository_event import (
    RepositoryEvent as RepositoryEventModel,
)


class TestProcessEvent:

    @pytest.fixture
    def dispatcher(self):
        return EventDispatcher()

    @pytest.fixture
    def mock_event_data(self):
        return RepositoryEventModel(
            provider="github",
            type="pull_request",
            action="opened",
            repository_full_name="test/repo",
            number=1,
            title="Test PR",
            payload={
                "pull_request": {"merged": False, "draft": False},
                "sender": {"id": 123, "login": "testuser"},
                "repository": {
                    "id": 456,
                    "full_name": "test/repo",
                    "name": "repo",
                    "owner": {"login": "test"},
                },
            },
        )

    @pytest.mark.asyncio
    @patch("src.events.dispatcher.event_hooks")
    @patch("src.events.dispatcher.logger")
    async def test_broadcasts_event_to_subscribers(
        self, mock_logger, mock_event_hooks, dispatcher, mock_event_data
    ):
        mock_event_hooks.broadcast_event = AsyncMock(return_value={})
        event = RepositoryEvent(mock_event_data)

        await dispatcher._process_event(event)

        mock_event_hooks.broadcast_event.assert_called_once()
        call_kwargs = mock_event_hooks.broadcast_event.call_args[1]
        assert call_kwargs["event_type"] == "pull_request.opened"
        assert call_kwargs["source_plugin"] == "sourceant_core"

    @pytest.mark.asyncio
    @patch("src.events.dispatcher.event_hooks")
    @patch("src.events.dispatcher.logger")
    async def test_extracts_user_context_from_payload(
        self, mock_logger, mock_event_hooks, dispatcher, mock_event_data
    ):
        mock_event_hooks.broadcast_event = AsyncMock(return_value={})
        event = RepositoryEvent(mock_event_data)

        await dispatcher._process_event(event)

        call_kwargs = mock_event_hooks.broadcast_event.call_args[1]
        event_data = call_kwargs["event_data"]
        assert event_data["user_context"]["github_id"] == 123
        assert event_data["user_context"]["username"] == "testuser"

    @pytest.mark.asyncio
    @patch("src.events.dispatcher.event_hooks")
    @patch("src.events.dispatcher.logger")
    async def test_extracts_repository_context_from_payload(
        self, mock_logger, mock_event_hooks, dispatcher, mock_event_data
    ):
        mock_event_hooks.broadcast_event = AsyncMock(return_value={})
        event = RepositoryEvent(mock_event_data)

        await dispatcher._process_event(event)

        call_kwargs = mock_event_hooks.broadcast_event.call_args[1]
        event_data = call_kwargs["event_data"]
        assert event_data["repository_context"]["github_repo_id"] == 456
        assert event_data["repository_context"]["full_name"] == "test/repo"

    @pytest.mark.asyncio
    @patch("src.events.dispatcher.event_hooks")
    @patch("src.events.dispatcher.logger")
    async def test_handles_event_without_action(
        self, mock_logger, mock_event_hooks, dispatcher
    ):
        mock_event_hooks.broadcast_event = AsyncMock(return_value={})
        event_data = RepositoryEventModel(
            provider="github",
            type="push",
            action=None,
            repository_full_name="test/repo",
            number=None,
            title=None,
            payload={
                "sender": {"id": 123, "login": "testuser"},
                "repository": {
                    "id": 456,
                    "full_name": "test/repo",
                    "name": "repo",
                    "owner": {"login": "test"},
                },
            },
        )
        event = RepositoryEvent(event_data)

        await dispatcher._process_event(event)

        call_kwargs = mock_event_hooks.broadcast_event.call_args[1]
        assert call_kwargs["event_type"] == "push"

    @pytest.mark.asyncio
    @patch("src.events.dispatcher.event_hooks")
    @patch("src.events.dispatcher.logger")
    async def test_logs_broadcast_error(
        self, mock_logger, mock_event_hooks, dispatcher, mock_event_data
    ):
        mock_event_hooks.broadcast_event = AsyncMock(
            side_effect=Exception("Broadcast failed")
        )
        event = RepositoryEvent(mock_event_data)

        await dispatcher._process_event(event)

        mock_logger.error.assert_called()
