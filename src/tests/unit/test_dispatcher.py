import pytest
from unittest.mock import MagicMock, patch
from fastapi import BackgroundTasks
from src.events.dispatcher import DELIVERY_TIMEOUT, EventDispatcher, bg_tasks_cv
from src.events.repository_event import RepositoryEvent


class TestDispatcher:
    @pytest.fixture
    def dispatcher(self):
        with patch("src.events.dispatcher.q", new=None):
            yield EventDispatcher()

    def test_dispatch_request_mode(self, monkeypatch, dispatcher):
        monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "request")

        mock_background_tasks = BackgroundTasks()
        mock_background_tasks.add_task = MagicMock()

        bg_tasks_cv.set(mock_background_tasks)

        dummy_event = RepositoryEvent(MagicMock())

        dispatcher.dispatch(dummy_event)

        mock_background_tasks.add_task.assert_called_once_with(
            dispatcher._process_event_sync, dummy_event
        )

    def test_dispatch_uses_redis_when_mode_is_redis(self, monkeypatch):
        monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "redis")

        with patch("src.events.dispatcher.q") as mock_q:
            dispatcher = EventDispatcher()
            dummy_event = RepositoryEvent(MagicMock())

            dispatcher.dispatch(dummy_event)

            mock_q.enqueue.assert_called_once_with(
                dispatcher._process_event_sync,
                dummy_event,
                job_timeout=DELIVERY_TIMEOUT,
            )

    def test_dispatch_uses_redislite_when_mode_is_redislite(self, monkeypatch):
        monkeypatch.setattr("src.events.dispatcher.QUEUE_MODE", "redislite")

        with patch("src.events.dispatcher.q") as mock_q:
            dispatcher = EventDispatcher()
            dummy_event = RepositoryEvent(MagicMock())

            dispatcher.dispatch(dummy_event)

            mock_q.enqueue.assert_called_once_with(
                dispatcher._process_event_sync,
                dummy_event,
                job_timeout=DELIVERY_TIMEOUT,
            )


def test_a_delivery_is_given_longer_than_the_queue_would_allow_by_default():
    """The queue stops a job at 180 seconds unless told otherwise, and a review
    of a large change asks a model several times and takes longer than that."""
    assert DELIVERY_TIMEOUT > 180
