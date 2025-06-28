from contextvars import ContextVar
from typing import List, Optional

import redis
from redislite import Redis as RedisLite

from fastapi import BackgroundTasks
from rq import Queue

from src.config.settings import QUEUE_MODE, REDIS_HOST, REDIS_PORT
from src.events.event import Event
from src.events.repository_event import RepositoryEvent
from src.integrations.github.github import GitHub
from src.llms.llm_factory import llm
from src.models.code_review import CodeReview, CodeSuggestion, Verdict
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.models.repository_event import RepositoryEvent as RepositoryEventModel
from src.utils.diff import get_diff
from src.utils.diff_parser import parse_diff, ParsedDiff
from src.utils.logger import logger

# Context variable to hold the BackgroundTasks object for the current request
bg_tasks_cv: ContextVar[Optional[BackgroundTasks]] = ContextVar(
    "bg_tasks", default=None
)

q = None
if QUEUE_MODE == "redis":
    logger.info("Using Redis for event queue.")
    redis_conn = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
    q = Queue(connection=redis_conn)
elif QUEUE_MODE == "redislite":
    logger.info("Using RedisLite for event queue.")
    # RedisLite uses a file-based Redis instance, no host/port needed.
    redis_conn = RedisLite()
    q = Queue(connection=redis_conn)
elif QUEUE_MODE == "request":
    logger.info("Using request-scoped background tasks for event processing.")
else:
    # This case should ideally not be reached if QUEUE_MODE is validated on startup,
    # but as a safeguard:
    logger.info("No queue mode configured. Events will not be dispatched.")


class EventDispatcher:
    """Dispatches events to the queue."""

    def dispatch(self, event: Event):
        """Dispatches an event to the configured queue or background task runner."""
        logger.info(f"Dispatching event: {event} (mode: {QUEUE_MODE})")
        if QUEUE_MODE in ["redis", "redislite"]:
            if not q:
                raise RuntimeError(f"{QUEUE_MODE} queue not initialized.")
            q.enqueue(self._process_event, event)

        elif QUEUE_MODE == "request":
            background_tasks = bg_tasks_cv.get()
            if not background_tasks:
                raise RuntimeError(
                    "FastAPI BackgroundTasks not found in context. Is the endpoint setting it?"
                )
            background_tasks.add_task(self._process_event, event)
        else:
            raise ValueError(
                f"Unknown QUEUE_MODE: '{QUEUE_MODE}'. Must be 'redis', 'redislite', or 'request'."
            )

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
                if repository_event.type in ("pull_request", "push"):
                    raw_diff = get_diff(repository_event)
                    if not raw_diff:
                        logger.info("No diff computed.")
                        return

                    # Dynamic Review Logic
                    total_tokens = llm().count_tokens(raw_diff)
                    logger.info(f"Total tokens in diff: {total_tokens}")

                    parsed_files: List[ParsedDiff] = parse_diff(raw_diff)
                    all_suggestions: List[CodeSuggestion] = []

                    if total_tokens < llm().token_limit:
                        logger.info("Diff is small enough for a single-pass review.")
                        # Send the full diff for a holistic review
                        full_review = llm().generate_code_review(diff=raw_diff)
                        if full_review and full_review.code_suggestions:
                            # Validate and enrich suggestions
                            for suggestion in full_review.code_suggestions:
                                for parsed_file in parsed_files:
                                    if suggestion.file_name == parsed_file.file_path:
                                        line = suggestion.line
                                        side = (
                                            suggestion.side.value
                                            if suggestion.side
                                            else "RIGHT"
                                        )
                                        if (
                                            line,
                                            side,
                                        ) in parsed_file.commentable_lines:
                                            position = parsed_file.line_to_position[
                                                (line, side)
                                            ]
                                            suggestion.position = position
                                            all_suggestions.append(suggestion)
                                        else:
                                            logger.warning(
                                                f"LLM hallucinated a suggestion for an un-changed line: {suggestion.file_name}:{line}"
                                            )
                                        break  # Move to the next suggestion
                        summary = full_review.summary if full_review else ""
                    else:
                        logger.info(
                            "Diff is too large. Performing file-by-file review."
                        )
                        # Generate a global summary to use as context for each file
                        global_summary_prompt = f"Provide a brief, one-paragraph summary of the following code changes:\n\n{raw_diff}"
                        global_context = llm().generate_text(global_summary_prompt)

                        for parsed_file in parsed_files:
                            logger.info(f"Reviewing file: {parsed_file.file_path}")
                            context = f"**Overall PR Context:**\n{global_context}\n\n**Current File:** `{parsed_file.file_path}`\nFocus only on the changes in this file."
                            review_for_file = llm().generate_code_review(
                                diff=parsed_file.diff_text, context=context
                            )

                            if (
                                not review_for_file
                                or not review_for_file.code_suggestions
                            ):
                                continue

                            for suggestion in review_for_file.code_suggestions:
                                line = suggestion.line
                                side = (
                                    suggestion.side.value
                                    if suggestion.side
                                    else "RIGHT"
                                )
                                if (line, side) in parsed_file.commentable_lines:
                                    position = parsed_file.line_to_position[
                                        (line, side)
                                    ]
                                    suggestion.position = position
                                    suggestion.file_name = parsed_file.file_path
                                    all_suggestions.append(suggestion)
                                else:
                                    logger.warning(
                                        f"LLM hallucinated a suggestion for an un-changed line: {parsed_file.file_path}:{line}"
                                    )
                        summary = llm().generate_summary(all_suggestions)

                    # Determine final verdict and create the review object
                    verdict = (
                        Verdict.REQUEST_CHANGES if all_suggestions else Verdict.APPROVE
                    )

                    final_review = CodeReview(
                        summary=summary,
                        verdict=verdict,
                        code_suggestions=all_suggestions,
                    )

                    logger.info("Scheduling review posting.")
                    if QUEUE_MODE == "redis":
                        if not q:
                            raise RuntimeError("Redis queue not initialized.")
                        q.enqueue(
                            self._post_review, repository, pull_request, final_review
                        )
                    else:  # In fastapi mode, this runs in the same background task.
                        self._post_review(repository, pull_request, final_review)

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
