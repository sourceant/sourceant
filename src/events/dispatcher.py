import redis
from rq import Queue
from src.events.event import Event
from src.events.repository_event import RepositoryEvent
from src.models.repository_event import RepositoryEvent as RepositoryEventModel
from src.utils.diff import get_diff
from src.utils.logger import logger
import os
from dotenv import load_dotenv

load_dotenv()

redis_conn = redis.Redis(
    host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=0
)
q = Queue(connection=redis_conn)


class EventDispatcher:
    """Dispatches events to the queue."""

    def dispatch(self, event: Event):
        """Dispatches an event to the queue."""
        logger.info(f"Dispatching event: {event}")
        q.enqueue(self._process_event, event)

    def _process_event(self, event: Event):
        if isinstance(event, RepositoryEvent):
            logger.info(f"Processing repository event: {event}")
            repository_event: RepositoryEventModel = event.data
            if not repository_event or not isinstance(
                repository_event, RepositoryEventModel
            ):
                logger.error("Invalid event data. Cannot process event.")
                return
            try:
                if repository_event.type == "push":
                    diff = get_diff(repository_event)
                    if diff:
                        logger.info(f"Diff computed: {diff}")
                    else:
                        logger.info("No diff computed.")
            except Exception as e:
                logger.exception(f"An error occurred while processing event: {e}")

        else:
            logger.error(f"Unhandled event type: {event}")
