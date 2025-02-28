import redis
from rq import Queue
from src.events.event import Event
from src.events.repository_event import RepositoryEvent
from src.models.repository_event import RepositoryEvent as RepositoryEventModel
from src.models.code_review import CodeReview
from src.models.repository import Repository
from src.models.pull_request import PullRequest
from src.integrations.github.github import GitHub
from src.utils.diff import get_diff
from src.utils.logger import logger
from src.llms.llm_factory import llm
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
            repository: Repository = Repository(
                name=repository_event.repository_full_name.split("/")[1],
                owner=repository_event.repository_full_name.split("/")[0],
            )
            pull_request: PullRequest = PullRequest(
                number=repository_event.number, title=repository_event.title
            )
            try:
                if repository_event.type in ("pull_request"):
                    diff = get_diff(repository_event)
                    if diff:
                        logger.debug(f"Diff computed: {diff}")
                        review: CodeReview = llm().generate_code_review(diff=diff)
                        logger.info("Post review to repository has been scheduled.")
                        q.enqueue(self._post_review, repository, pull_request, review)
                    else:
                        logger.info("No diff computed.")
            except Exception as e:
                logger.exception(f"An error occurred while processing event: {e}")

        else:
            logger.error(f"Unhandled event type: {event}")

    def _post_review(
        self, repository: Repository, pull_request: PullRequest, review: CodeReview
    ):
        """Posts the review to the repository."""
        GitHub().post_review(
            repository=repository, pull_request=pull_request, code_review=review
        )
