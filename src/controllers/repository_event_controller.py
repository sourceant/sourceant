from src.models.repository_event import RepositoryEvent as RepositoryEventModel
from src.controllers.base_controller import BaseController
import traceback

from src.events.dispatcher import EventDispatcher
from src.events.repository_event import RepositoryEvent
from src.utils.logger import logger

dispatcher = EventDispatcher()


class RepositoryEventController(BaseController):

    @classmethod
    def index(cls):
        try:
            events = RepositoryEventModel.get_all()
            if not events:
                return cls().success([])
            events = [event.dict() for event in events]
            return cls().success(events)
        except Exception:
            return cls().failure(
                error=traceback.format_exc(),
                message="An error occurred while retrieving repository events",
                status_code=500,
            )

    @classmethod
    def show(cls, event_id: int):
        try:
            event = RepositoryEventModel.get_by_id(event_id)
            if not event:
                return cls().failure("Event not found", status_code=404)
            return cls().success(event)
        except Exception:
            return cls().failure(
                message="An error occurred while retrieving the repository event",
                status_code=500,
                error=traceback.format_exc(),
            )

    @classmethod
    def create(
        cls,
        action: str,
        type: str,
        url: str,
        number: int,
        title: str,
        repository: str,
        payload: dict,
    ):
        try:
            event = RepositoryEventModel(
                action=action,
                type=type,
                url=url,
                number=number,
                title=title,
                repository=repository,
                payload=payload,
            ).save()
            logger.info(f"Repository event created: {RepositoryEvent(event)}")
            dispatcher.dispatch(RepositoryEvent(event))
            return cls().success(
                event.dict(), "Repository event recorded.", status_code=201
            )
        except Exception:
            logger.error(traceback.format_exc())
            return cls().failure(
                message="An error occurred while creating a repository event",
                status_code=500,
                error=traceback.format_exc(),
            )

    @classmethod
    def destroy(cls, event_id: int):
        try:
            event = RepositoryEventModel.get_by_id(event_id)
            if not event:
                return cls().failure("Event not found", status_code=404)
            event.delete()
            return cls().success({"message": "Event deleted"})
        except Exception:
            return cls().failure(
                message="An error occurred while deleting the repository event",
                status_code=500,
                error=traceback.format_exc(),
            )
