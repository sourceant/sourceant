import tempfile
from contextvars import ContextVar
from pathlib import Path
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
    APP_ENV,
)
from src.events.event import Event
from src.events.repository_event import RepositoryEvent
from src.integrations.github.github import GitHub
from src.llms.llm_factory import llm
from src.models.code_review import CodeReview, Verdict, SuggestionCategory
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.models.repository_event import RepositoryEvent as RepositoryEventModel

from src.utils.diff_parser import parse_diff, ParsedDiff
from src.utils.line_mapper import LineMapper
from src.utils.suggestion_filter import SuggestionFilter

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
        if not isinstance(event, RepositoryEvent):
            logger.info(f"Skipping non-repository event: {event}")
            return

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
        if not isinstance(event, RepositoryEvent):
            logger.error(f"Unhandled event type: {event}")
            return

        logger.info(
            f"Processing repository event: {event.data.type} on {event.data.repository_full_name}"
        )

        # Define specific events that should trigger PR reviews
        reviewable_events = {
            "pull_request": {"opened", "synchronize", "reopened", "ready_for_review"}
        }

        if event.data.type not in reviewable_events:
            logger.info(
                f"Skipping event of type '{event.data.type}'. Not a reviewable event type."
            )
            return

        if event.data.action not in reviewable_events[event.data.type]:
            logger.info(
                f"Skipping '{event.data.type}.{event.data.action}' action. Only actions {reviewable_events[event.data.type]} trigger reviews."
            )
            return

        repository_event: RepositoryEventModel = event.data
        if not repository_event or not isinstance(
            repository_event, RepositoryEventModel
        ):
            logger.error("Invalid event data. Cannot process event.")
            return

        repository = Repository(
            name=repository_event.repository_full_name.split("/")[1],
            owner=repository_event.repository_full_name.split("/")[0],
        )
        pull_request_payload = repository_event.payload.get("pull_request") or {}

        head_sha = pull_request_payload.get("head", {}).get("sha")
        base_sha = pull_request_payload.get("base", {}).get("sha")

        pull_request = PullRequest(
            number=repository_event.number,
            title=repository_event.title,
            draft=pull_request_payload.get("draft", False),
            merged=pull_request_payload.get("merged", False),
            base_sha=base_sha,
            head_sha=head_sha,
        )

        if pull_request.merged:
            logger.info(
                f"Pull request #{pull_request.number} is already merged. Skipping."
            )
            return

        if pull_request.draft and not REVIEW_DRAFT_PRS:
            logger.info(
                f"Pull request #{pull_request.number} is a draft. Skipping review."
            )
            return

        owner, repo_name = repository.owner, repository.name
        github = GitHub()

        try:
            raw_diff = github.get_diff(
                owner=owner,
                repo=repo_name,
                pr_number=pull_request.number,
                base_sha=pull_request.base_sha,
                head_sha=pull_request.head_sha,
            )
            if not raw_diff:
                logger.info("No diff could be computed. Skipping.")
                return

            parsed_files = parse_diff(raw_diff)
            line_mapper = LineMapper(parsed_files)
            # Initial token count from the diff itself, using the LLM's tokenizer for accuracy.
            total_tokens = sum(llm().count_tokens(pf.diff_text) for pf in parsed_files)

            # If uploads are disabled, account for the full file content that will be added to the prompt
            if not llm().uploads_enabled:
                logger.info(
                    "File uploads disabled. Calculating tokens from full file content."
                )
                for pf in parsed_files:
                    content = github.get_file_content(
                        owner=owner,
                        repo=repo_name,
                        file_path=pf.file_path,
                        sha=pull_request.head_sha,
                    )
                    if content:
                        # Use the LLM's own tokenizer for accuracy if available
                        total_tokens += llm().count_tokens(content)
            logger.info(f"Total tokens in diff: {total_tokens}")

            suggestion_filter = SuggestionFilter()

            if total_tokens < llm().token_limit:
                logger.info("Diff is small enough for a single-pass review.")
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_file_paths = self._prepare_llm_file_context(
                        github, owner, repo_name, pull_request, parsed_files, temp_dir
                    )
                    full_review = llm().generate_code_review(
                        diff=raw_diff,
                        parsed_files=parsed_files,
                        file_paths=temp_file_paths,
                    )

                all_suggestions = []
                if full_review and full_review.code_suggestions:
                    all_suggestions = self._process_suggestions(
                        full_review.code_suggestions, suggestion_filter, line_mapper
                    )

                verdict = self._determine_verdict_from_suggestions(all_suggestions)
                if full_review:
                    final_review = CodeReview(
                        summary=full_review.summary,
                        verdict=verdict,
                        code_suggestions=all_suggestions,
                        scores=full_review.scores,
                    )
                else:
                    final_review = CodeReview(
                        summary=None,
                        verdict=verdict,
                        code_suggestions=all_suggestions,
                    )
            else:
                logger.info("Diff is too large. Performing file-by-file review.")
                all_suggestions = []
                for parsed_file in parsed_files:
                    logger.info(f"Reviewing file: {parsed_file.file_path}")
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_file_paths = self._prepare_llm_file_context(
                            github,
                            owner,
                            repo_name,
                            pull_request,
                            [parsed_file],
                            temp_dir,
                        )
                        review_for_file = llm().generate_code_review(
                            diff=parsed_file.diff_text,
                            parsed_files=[parsed_file],
                            file_paths=temp_file_paths,
                        )

                    if review_for_file and review_for_file.code_suggestions:
                        all_suggestions.extend(
                            self._process_suggestions(
                                review_for_file.code_suggestions,
                                suggestion_filter,
                                line_mapper,
                            )
                        )

                summary_obj = llm().generate_summary(all_suggestions)
                verdict = self._determine_verdict_from_suggestions(all_suggestions)
                final_review = CodeReview(
                    summary=summary_obj,
                    verdict=verdict,
                    code_suggestions=all_suggestions,
                )

            self._schedule_review_posting(
                repository, pull_request, final_review, line_mapper
            )

        except Exception as e:
            logger.exception(f"An error occurred while processing event: {e}")

    def _process_suggestions(
        self,
        suggestions: List,
        suggestion_filter: SuggestionFilter,
        line_mapper: LineMapper,
    ) -> List:
        """Filter and map suggestions to valid diff positions."""
        result = []
        filtered, _ = suggestion_filter.filter_suggestions(suggestions)
        for suggestion in filtered:
            mapped_result = line_mapper.validate_and_map_suggestion(
                suggestion, strict_mode=(APP_ENV == "production")
            )
            if mapped_result:
                suggestion.position = mapped_result[0]
                result.append(suggestion)
        return result

    def _post_review(
        self,
        repository: Repository,
        pull_request: PullRequest,
        review: CodeReview,
        line_mapper: LineMapper,
    ):
        """Posts the review to the repository."""
        GitHub().post_review(
            repository=repository,
            pull_request=pull_request,
            code_review=review,
            line_mapper=line_mapper,
        )

    def _determine_verdict_from_suggestions(self, suggestions: List) -> Verdict:
        """Determines the appropriate verdict based on suggestions analysis."""
        if not suggestions:
            return Verdict.APPROVE

        critical_categories = {SuggestionCategory.BUG, SuggestionCategory.SECURITY}
        security_keywords = ["vulnerability", "exploit", "injection"]

        critical_count = 0
        for suggestion in suggestions:
            if not suggestion or not suggestion.comment:
                continue

            comment_lower = suggestion.comment.lower()

            if suggestion.category in critical_categories or any(
                keyword in comment_lower for keyword in security_keywords
            ):
                critical_count += 1

        if critical_count > 0:
            return Verdict.REQUEST_CHANGES
        else:
            return Verdict.COMMENT

    def _schedule_review_posting(
        self,
        repository: Repository,
        pull_request: PullRequest,
        review: CodeReview,
        line_mapper: LineMapper,
    ):
        """Schedules the review posting."""
        if QUEUE_MODE in ["redis", "redislite"]:
            if not q:
                raise RuntimeError(f"{QUEUE_MODE} queue not initialized.")
            q.enqueue(self._post_review, repository, pull_request, review, line_mapper)
        else:  # In fastapi mode, this runs in the same background task.
            self._post_review(repository, pull_request, review, line_mapper)

    def _prepare_llm_file_context(
        self,
        github: GitHub,
        owner: str,
        repo_name: str,
        pull_request: PullRequest,
        parsed_files: List[ParsedDiff],
        temp_dir: str,
    ) -> List[str]:
        """Prepares file context for the LLM by creating temporary files."""
        temp_file_paths = []
        if pull_request.head_sha:
            for pf in parsed_files:
                content = github.get_file_content(
                    owner=owner,
                    repo=repo_name,
                    file_path=pf.file_path,
                    sha=pull_request.head_sha,
                )
                if content:
                    temp_file_path = Path(temp_dir) / pf.file_path
                    temp_file_path.parent.mkdir(parents=True, exist_ok=True)
                    temp_file_path.write_text(content)
                    temp_file_paths.append(str(temp_file_path))
        return temp_file_paths
