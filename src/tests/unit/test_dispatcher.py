import pytest
from unittest.mock import MagicMock, patch
from fastapi import BackgroundTasks
from src.events.dispatcher import EventDispatcher, bg_tasks_cv
from src.events.repository_event import RepositoryEvent


class TestDispatcher:
    @pytest.fixture
    def dispatcher(self):
        with patch("src.events.dispatcher.q", new=None):
            yield EventDispatcher()

    def test_dispatch_request_mode(self, monkeypatch, dispatcher):
        """
        Tests that the dispatcher uses the BackgroundTasks from the context variable
        when QUEUE_MODE is 'request'.
        """
        monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "request")

        mock_background_tasks = BackgroundTasks()
        mock_background_tasks.add_task = MagicMock()

        bg_tasks_cv.set(mock_background_tasks)

        dummy_event = RepositoryEvent(MagicMock())

        dispatcher.dispatch(dummy_event)

        mock_background_tasks.add_task.assert_called_once_with(
            dispatcher._process_event, dummy_event
        )

    def test_dispatch_uses_redis_when_mode_is_redis(self, monkeypatch):
        """
        Tests that the dispatcher uses the Redis queue (rq)
        when QUEUE_MODE is 'redis'.
        """
        monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "redis")

        # Mock the redis queue object 'q' in the dispatcher's module
        with patch("src.events.dispatcher.q") as mock_q:
            dispatcher = EventDispatcher()
            dummy_event = RepositoryEvent(MagicMock())

            dispatcher.dispatch(dummy_event)

            mock_q.enqueue.assert_called_once_with(
                dispatcher._process_event, dummy_event
            )
