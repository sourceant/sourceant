from contextvars import ContextVar
from typing import List, Optional

import redis
from redislite import Redis as RedisLite

from fastapi import BackgroundTasks
from rq import Queue

from src.config.settings import (
    QUEUE_MODE,
    REDIS_HOST,
    REDIS_PORT,
    REVIEW_DRAFT_PRS,
    DEBUG_MODE,
    APP_ENV,
)
from src.events.event import Event
from src.events.repository_event import RepositoryEvent
from src.integrations.github.github import GitHub
from src.llms.llm_factory import llm
from src.models.code_review import CodeReview, CodeSuggestion, Verdict
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.models.repository_event import RepositoryEvent as RepositoryEventModel

from src.utils.diff_parser import parse_diff, ParsedDiff
from src.utils.line_mapper import LineMapper
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
            is_draft = repository_event.payload.get("pull_request", {}).get(
                "draft", False
            )
            is_merged = repository_event.payload.get("pull_request", {}).get(
                "merged", False
            )

            pull_request: PullRequest = PullRequest(
                number=repository_event.number,
                title=repository_event.title,
                draft=is_draft,
                merged=is_merged,
            )

            if pull_request.merged:
                logger.info(f"Skipping review for merged PR #{pull_request.number}")
                return

            if pull_request.draft and not REVIEW_DRAFT_PRS:
                logger.info(f"Skipping review for draft PR #{pull_request.number}")
                return

            try:
                if repository_event.type not in ["pull_request", "push"]:
                    logger.info(
                        f"Skipping event of type '{repository_event.type}' because it is not a pull request or push."
                    )
                    return

                github = GitHub()
                owner = repository.owner
                repo_name = repository.name
                raw_diff = ""

                if repository_event.type == "push":
                    base_sha = repository_event.payload.get("before")
                    head_sha = repository_event.payload.get("after")
                    if not base_sha or not head_sha:
                        logger.error("Missing 'before' or 'after' SHA for push event.")
                        return
                    raw_diff = github.get_diff_between_shas(
                        owner, repo_name, base_sha, head_sha
                    )
                elif repository_event.type == "pull_request":
                    raw_diff = github.get_diff(
                        owner=owner, repo=repo_name, pr_number=pull_request.number
                    )

                if not raw_diff:
                    logger.info("No diff could be computed.")
                    return

                # Dynamic Review Logic
                total_tokens = llm().count_tokens(raw_diff)
                logger.info(f"Total tokens in diff: {total_tokens}")

                parsed_files: List[ParsedDiff] = parse_diff(raw_diff)
                all_suggestions: List[CodeSuggestion] = []

                # Create line mapper for better line number handling
                line_mapper = LineMapper(parsed_files)

                # Log diff structure for debugging
                if DEBUG_MODE:
                    logger.debug(
                        f"Diff structure:\n{line_mapper.generate_line_mapping_report()}"
                    )

                if total_tokens < llm().token_limit:
                    logger.info("Diff is small enough for a single-pass review.")

                    # Add enhanced context to the diff
                    enhanced_context = line_mapper.get_enhanced_diff_context()

                    # Send the full diff for a holistic review
                    full_review = llm().generate_code_review(
                        diff=raw_diff, context=enhanced_context
                    )
                    if full_review and full_review.code_suggestions:
                        # Validate and enrich suggestions with the LineMapper utility
                        for suggestion in full_review.code_suggestions:
                            mapped_result = line_mapper.validate_and_map_suggestion(
                                suggestion, strict_mode=(APP_ENV == "production")
                            )
                            if mapped_result:
                                position, _ = mapped_result
                                suggestion.position = position
                                all_suggestions.append(suggestion)
                    summary = full_review.summary if full_review else ""

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
                    if QUEUE_MODE in ["redis", "redislite"]:
                        if not q:
                            raise RuntimeError(f"{QUEUE_MODE} queue not initialized.")
                        q.enqueue(
                            self._post_review, repository, pull_request, final_review
                        )
                    else:  # In fastapi mode, this runs in the same background task.
                        self._post_review(repository, pull_request, final_review)
                else:
                    logger.info("Diff is too large. Performing file-by-file review.")
                    # Generate a global summary to use as context for each file
                    global_summary_prompt = f"Provide a brief, one-paragraph summary of the following code changes:\n\n{raw_diff}"
                    global_context = llm().generate_text(global_summary_prompt)

                    for parsed_file in parsed_files:
                        logger.info(f"Reviewing file: {parsed_file.file_path}")
                        context = f"**Overall PR Context:**\n{global_context}\n\n**Current File:** `{parsed_file.file_path}`\nFocus only on the changes in this file."
                        review_for_file = llm().generate_code_review(
                            diff=parsed_file.diff_text, context=context
                        )

                        if not review_for_file or not review_for_file.code_suggestions:
                            continue

                        for suggestion in review_for_file.code_suggestions:
                            # The file_name might be missing from the LLM response in file-by-file mode
                            if not suggestion.file_name:
                                suggestion.file_name = parsed_file.file_path

                            mapped_result = line_mapper.validate_and_map_suggestion(
                                suggestion, strict_mode=(APP_ENV == "production")
                            )
                            if mapped_result:
                                position, _ = mapped_result
                                suggestion.position = position
                                all_suggestions.append(suggestion)
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
