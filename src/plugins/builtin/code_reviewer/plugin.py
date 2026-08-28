"""
Code Reviewer Plugin for SourceAnt.

Subscribes to pull request events and generates automated code reviews.
"""

import difflib
import re
from typing import Dict, Any, Optional, List

from rapidfuzz import fuzz

from src.core.plugins import BasePlugin, PluginMetadata, PluginType
from src.core.plugins import event_hooks, HookPriority
from sqlalchemy.exc import SQLAlchemyError

from src.core.code_index import CodeIndexReader, SQLCodeIndexRepository
from src.core.change_context import (
    ChangeContextResolver,
    ChangedFile,
    ChangeSet,
    DefaultChangeContextResolver,
)
from src.core.knowledge import (
    KnowledgeReader,
    KnowledgeSelector,
    LinkedKnowledgeSelector,
)
from src.core.impact import ChangeImpactResolver
from src.core.requirements import (
    LinkedRequirementSelector,
    RequirementSelector,
    RequirementsReader,
)
from src.core.review_context import (
    DefaultReviewCodeContextPreparer,
    LazyChangedFileCodeIndex,
    merge_review_code_contexts,
)
from src.core.scope import Scope
from src.core.settings.resolver import value_of
from src.core.review_evidence import (
    CachedChangedFileEvidenceReader,
    ChangedFileEvidenceReader,
    FallbackChangedFileEvidenceReader,
    IndexedChangedFileEvidenceReader,
    StructuralReviewEvidenceValidator,
)
from src.integrations.github.github import GitHub
from src.llms.llm_factory import llm
from src.models.code_review import (
    CodeReview,
    CodeReviewSummary,
    Side,
    Verdict,
    SuggestionCategory,
)
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.utils.diff_parser import parse_diff, ParsedDiff
from src.utils.line_mapper import LineMapper
from src.utils.suggestion_filter import SuggestionFilter
from src.guards.base import GuardAction
from src.guards.duplicate_approval import DuplicateApprovalGuard
from src.config.settings import REVIEW_DRAFT_PRS, APP_ENV
from src.utils.logger import logger
from src.utils.review_record_service import get_last_reviewed_sha, save_review_record


class CodeReviewerPlugin(BasePlugin):
    """
    Code Reviewer Plugin that subscribes to pull request events.

    Generates automated code reviews using LLMs and posts them to GitHub.
    Only processes GitHub App events (not OAuth events which are for activity tracking).
    """

    _plugin_name = "code_reviewer"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the Code Reviewer plugin."""
        super().__init__(config)

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="code_reviewer",
            version="1.0.0",
            description="Automated code review generation for pull requests",
            author="SourceAnt Team",
            plugin_type=PluginType.INTEGRATION,
            dependencies=[],
            config_schema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable/disable code reviews",
                        "default": True,
                    },
                    "review_draft_prs": {
                        "type": "boolean",
                        "description": "Review draft pull requests",
                        "default": False,
                    },
                },
            },
            enabled=True,
            priority=25,  # High priority for core functionality
        )

    async def _initialize(self) -> None:
        """Initialize the plugin."""
        logger.info("Initializing Code Reviewer plugin")

        # Subscribe to pull request events
        event_hooks.subscribe_to_events(
            plugin_name=self.metadata.name,
            callback=self._handle_event,
            event_types=[
                "pull_request.opened",
                "pull_request.synchronize",
                "pull_request.reopened",
                "pull_request.ready_for_review",
            ],
        )

        logger.info("Code Reviewer plugin initialized and subscribed to PR events")

    async def _start(self) -> None:
        """Start the plugin."""
        logger.info("Starting Code Reviewer plugin")

        # Verify LLM is available
        try:
            llm_instance = llm()
            logger.info(f"Code Reviewer using LLM: {llm_instance.__class__.__name__}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM for code reviews: {e}")
            raise

        logger.info("Code Reviewer plugin started successfully")

    async def _stop(self) -> None:
        """Stop the plugin."""
        logger.info("Stopping Code Reviewer plugin")
        # No background tasks to stop
        logger.info("Code Reviewer plugin stopped")

    async def _cleanup(self) -> None:
        """Cleanup plugin resources."""
        logger.info("Cleaning up Code Reviewer plugin")
        # Event subscriptions will be cleaned up by plugin manager
        logger.info("Code Reviewer plugin cleanup completed")

    async def _handle_event(
        self, event_type: str, event_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle pull request events for code review.

        Args:
            event_type: Type of event (e.g., "pull_request.opened")
            event_data: Event data from broadcaster

        Returns:
            Processing result dictionary
        """
        try:
            # Only process GitHub App events (not OAuth activity tracking)
            auth_type = event_data.get("auth_type", "github_app")
            if auth_type != "github_app":
                logger.debug(
                    f"Skipping {event_type} from {auth_type} - Code Reviewer only processes GitHub App events"
                )
                return {
                    "processed": False,
                    "reason": "OAuth events not processed for reviews",
                }

            # Check if plugin is enabled
            if not self.get_config("enabled", True):
                return {"processed": False, "reason": "Code Reviewer plugin disabled"}

            # Extract event data
            repository_event = event_data.get("repository_event")
            user_context = event_data.get("user_context")
            repository_context = event_data.get("repository_context")
            payload = event_data.get("payload", {})

            if not repository_event or not repository_context:
                return {"processed": False, "reason": "Missing required event data"}

            logger.info(
                f"Processing {event_type} for PR #{repository_event.get('number')} "
                f"in {repository_context.get('full_name')}"
            )

            # Create model instances
            repository = Repository(
                name=repository_context["name"], owner=repository_context["owner"]
            )

            pull_request_payload = payload.get("pull_request", {})
            head_sha = pull_request_payload.get("head", {}).get("sha")
            base_sha = pull_request_payload.get("base", {}).get("sha")

            pull_request = PullRequest(
                number=repository_event.get("number"),
                title=repository_event.get("title"),
                draft=pull_request_payload.get("draft", False),
                merged=pull_request_payload.get("merged", False),
                base_sha=base_sha,
                head_sha=head_sha,
            )

            # Check if we should skip this PR
            skip_reason = self._should_skip_review(pull_request)
            if skip_reason:
                logger.info(f"Skipping review: {skip_reason}")
                return {"processed": False, "reason": skip_reason}

            pr_metadata = {
                "title": repository_event.get("title"),
                "description": pull_request_payload.get("body"),
                "number": repository_event.get("number"),
                "base_ref": pull_request_payload.get("base", {}).get("ref"),
                "head_ref": pull_request_payload.get("head", {}).get("ref"),
            }

            # Generate and post review
            review_result = await self.generate_review(
                repository,
                pull_request,
                pr_metadata=pr_metadata,
                event_type=event_type,
                repository_full_name=repository_context.get("full_name"),
            )

            # Broadcast review completion event
            if review_result.get("status") == "success":
                await event_hooks.broadcast_event(
                    event_type="sourceant.review_completed",
                    event_data={
                        "repository": repository_context,
                        "pull_request": {
                            "number": pull_request.number,
                            "title": pull_request.title,
                            "base_sha": pull_request.base_sha,
                            "head_sha": pull_request.head_sha,
                            "draft": pull_request.draft,
                        },
                        "user_context": user_context,
                        "review_result": review_result,
                        "original_event": event_data,
                    },
                    source_plugin=self.metadata.name,
                )

            return {
                "processed": True,
                "review_result": review_result,
                "pr_number": pull_request.number,
                "repository": repository_context.get("full_name"),
            }

        except Exception as e:
            logger.error(f"Error processing {event_type} event: {e}", exc_info=True)

            # Broadcast error event
            await event_hooks.broadcast_event(
                event_type="sourceant.review_failed",
                event_data={
                    "error": str(e),
                    "event_type": event_type,
                    "event_data": event_data,
                },
                source_plugin=self.metadata.name,
            )

            return {"processed": False, "error": str(e)}

    def _should_skip_review(self, pull_request: PullRequest) -> Optional[str]:
        """
        Check if we should skip reviewing this pull request.

        Args:
            pull_request: PullRequest instance

        Returns:
            Reason to skip or None if should proceed
        """
        if pull_request.merged:
            return f"Pull request #{pull_request.number} is already merged"

        if pull_request.draft and not self.get_config(
            "review_draft_prs", REVIEW_DRAFT_PRS
        ):
            return f"Pull request #{pull_request.number} is a draft"

        if not pull_request.number:
            return "Invalid pull request number"

        return None

    async def generate_review(
        self,
        repository: Repository,
        pull_request: PullRequest,
        pr_metadata: Optional[Dict[str, Any]] = None,
        event_type: Optional[str] = None,
        repository_full_name: Optional[str] = None,
        post: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate code review and post it to GitHub.

        Args:
            repository: Repository instance
            pull_request: PullRequest instance
            pr_metadata: Optional PR metadata dict
            event_type: Event type string (e.g. "pull_request.synchronize")
            repository_full_name: Full repo name (e.g. "owner/repo")

        Returns:
            Review generation and posting results
        """
        try:
            # Get GitHub client
            github = GitHub()

            repo_full_name = (
                repository_full_name or f"{repository.owner}/{repository.name}"
            )

            # Incremental review: on synchronize, only review new changes
            raw_diff = None
            if event_type == "pull_request.synchronize" and pull_request.head_sha:
                last_sha = get_last_reviewed_sha(repo_full_name, pull_request.number)
                if last_sha and last_sha != pull_request.head_sha:
                    try:
                        raw_diff = github.get_diff_between_shas(
                            owner=repository.owner,
                            repo=repository.name,
                            base_sha=last_sha,
                            head_sha=pull_request.head_sha,
                        )
                        logger.info(
                            f"Incremental review: diffing {last_sha[:8]}..{pull_request.head_sha[:8]}"
                        )
                    except ValueError:
                        logger.warning(
                            "Incremental diff failed (possible force push). Falling back to full diff."
                        )
                        raw_diff = None

            # Full diff fallback
            if not raw_diff:
                raw_diff = github.get_diff(
                    owner=repository.owner,
                    repo=repository.name,
                    pr_number=pull_request.number,
                    base_sha=pull_request.base_sha,
                    head_sha=pull_request.head_sha,
                )

            if not raw_diff:
                return {
                    "status": "error",
                    "message": "No diff could be computed",
                    "error_type": "no_diff",
                }

            # Parse diff and create line mapper
            parsed_files = parse_diff(raw_diff)
            line_mapper = LineMapper(parsed_files)

            # Calculate token count
            llm_instance = llm()
            total_tokens = sum(
                llm_instance.count_tokens(pf.diff_text) for pf in parsed_files
            )

            logger.info(f"Total tokens in diff: {total_tokens}")

            existing_comments = github.get_existing_bot_review_comments(
                repository.owner, repository.name, pull_request.number
            )
            content_cache: Dict[str, str | None] = {}

            def read_changed_file(path: str) -> str | None:
                if path not in content_cache:
                    content_cache[path] = github.get_file_content(
                        repository.owner,
                        repository.name,
                        path,
                        pull_request.head_sha,
                    )
                return content_cache[path]

            code_scope = Scope.from_mapping(
                {
                    "repository": repo_full_name,
                    "revision": pull_request.head_sha,
                }
            )
            context_file_limit = value_of(
                "review.structural_context_file_limit",
                repository=repo_full_name,
            )
            local_code = LazyChangedFileCodeIndex(
                code_scope,
                [
                    parsed_file.file_path
                    for parsed_file in parsed_files
                    if not parsed_file.is_binary_file
                ],
                read_changed_file,
                file_limit=context_file_limit,
            )
            try:
                durable_code = self.services.resolve(CodeIndexReader)
            except LookupError:
                durable_code = _core_code_index()
            changed_files = tuple(
                ChangedFile(path=parsed_file.file_path)
                for parsed_file in parsed_files
                if not parsed_file.is_binary_file and parsed_file.file_path
            )
            known = None
            if changed_files:
                known = self._change_context_resolver(durable_code).resolve(
                    ChangeSet(
                        scope=Scope.from_mapping({"repository": repo_full_name}),
                        files=changed_files,
                        revision=pull_request.head_sha or "",
                        base_revision=pull_request.base_sha or "",
                        title=pull_request.title or "",
                        description=getattr(pull_request, "body", "") or "",
                    )
                )
            requirements_section = _requirements_section(known)
            knowledge_section = _knowledge_section(known)
            impact_section = _impact_section(known)
            local_evidence = CachedChangedFileEvidenceReader(read_changed_file)
            evidence: ChangedFileEvidenceReader = local_evidence
            if durable_code is not None:
                evidence = FallbackChangedFileEvidenceReader(
                    IndexedChangedFileEvidenceReader(durable_code, code_scope),
                    local_evidence,
                )

            # Generate review based on token count
            if total_tokens < llm_instance.token_limit:
                logger.info("Diff is small enough for a single-pass review.")
                final_review = await self._generate_single_pass_review(
                    github,
                    repository,
                    pull_request,
                    raw_diff,
                    parsed_files,
                    line_mapper,
                    pr_metadata=pr_metadata,
                    existing_comments=existing_comments,
                    evidence=evidence,
                    code_readers=(durable_code, local_code),
                    read_content=read_changed_file,
                    context_file_limit=context_file_limit,
                    requirements=requirements_section,
                    knowledge=knowledge_section,
                    impact=impact_section,
                )
            else:
                logger.info("Diff is too large. Performing file-by-file review.")
                final_review = await self._generate_file_by_file_review(
                    github,
                    repository,
                    pull_request,
                    parsed_files,
                    line_mapper,
                    pr_metadata=pr_metadata,
                    existing_comments=existing_comments,
                    evidence=evidence,
                    code_readers=(durable_code, local_code),
                    read_content=read_changed_file,
                    context_file_limit=context_file_limit,
                    requirements=requirements_section,
                    knowledge=knowledge_section,
                    impact=impact_section,
                )

            if existing_comments and final_review.code_suggestions:
                before_count = len(final_review.code_suggestions)
                final_review.code_suggestions = self._filter_duplicate_suggestions(
                    final_review.code_suggestions, existing_comments
                )
                removed = before_count - len(final_review.code_suggestions)
                if removed:
                    logger.info(
                        f"Filtered {removed} duplicate suggestion(s) already posted on PR"
                    )
                    final_review.verdict = self._determine_verdict_from_suggestions(
                        final_review.code_suggestions
                    )

            # Apply review guards
            guards = [DuplicateApprovalGuard()]
            for guard in guards:
                result = guard.check(repository, pull_request, final_review, github)
                if result.action == GuardAction.BLOCK:
                    logger.info(f"Review blocked by guard: {result.reason}")
                    return {
                        "status": "blocked",
                        "message": result.reason,
                        "error_type": "guard_blocked",
                    }
                if result.review:
                    final_review = result.review
                    logger.info(f"Review modified by guard: {result.reason}")

            # Post review to GitHub, unless this is a preview run
            post_result = None
            if post:
                post_result = github.post_review(
                    repository=repository,
                    pull_request=pull_request,
                    code_review=final_review,
                    line_mapper=line_mapper,
                )
                if pull_request.head_sha and pull_request.base_sha:
                    save_review_record(
                        repo_full_name,
                        pull_request.number,
                        pull_request.head_sha,
                        pull_request.base_sha,
                    )

            logger.info(
                f"Review generation {'and posting ' if post else '(preview) '}"
                f"completed for PR #{pull_request.number}"
            )

            return {
                "status": "success",
                "review_posted": post_result,
                "review": (
                    final_review.model_dump(mode="json") if final_review else None
                ),
                "suggestions_count": (
                    len(final_review.code_suggestions)
                    if final_review.code_suggestions
                    else 0
                ),
                "verdict": (
                    final_review.verdict.value if final_review.verdict else "COMMENT"
                ),
                "total_tokens": total_tokens,
            }

        except Exception as e:
            logger.error(f"Error generating/posting review: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "error_type": "review_generation_failed",
            }

    async def _generate_single_pass_review(
        self,
        github: GitHub,
        repository: Repository,
        pull_request: PullRequest,
        raw_diff: str,
        parsed_files: List[ParsedDiff],
        line_mapper: LineMapper,
        pr_metadata: Optional[Dict[str, Any]] = None,
        existing_comments: Optional[List[Dict[str, Any]]] = None,
        evidence: ChangedFileEvidenceReader | None = None,
        code_readers: tuple[CodeIndexReader | None, CodeIndexReader] | None = None,
        read_content=None,
        context_file_limit: int = 20,
        requirements: Optional[str] = None,
        knowledge: Optional[str] = None,
        impact: Optional[str] = None,
    ) -> CodeReview:
        """Generate review in a single pass for small diffs."""
        suggestion_filter = SuggestionFilter()
        code_context = self._prepare_code_context(
            code_readers,
            repository.full_name or f"{repository.owner}/{repository.name}",
            pull_request.head_sha,
            [parsed_file.file_path for parsed_file in parsed_files],
            read_content=read_content,
            file_limit=context_file_limit,
        )

        full_review = llm().generate_code_review(
            diff=raw_diff,
            parsed_files=parsed_files,
            pr_metadata=pr_metadata,
            existing_comments=existing_comments,
            code_context=code_context,
            requirements=requirements,
            knowledge=knowledge,
            impact=impact,
        )

        all_suggestions = []
        evidence_rejections: List[str] = []
        if full_review and full_review.code_suggestions:
            all_suggestions = self._process_suggestions(
                full_review.code_suggestions,
                suggestion_filter,
                line_mapper,
                evidence=evidence,
                evidence_rejections=evidence_rejections,
            )

        verdict = self._determine_verdict_from_suggestions(all_suggestions)
        if full_review:
            return CodeReview(
                summary=(
                    self._summary_from_suggestions(all_suggestions)
                    if evidence_rejections
                    else full_review.summary
                ),
                verdict=verdict,
                code_suggestions=all_suggestions,
                scores=full_review.scores,
            )
        else:
            return CodeReview(
                summary=None,
                verdict=verdict,
                code_suggestions=all_suggestions,
            )

    async def _generate_file_by_file_review(
        self,
        github: GitHub,
        repository: Repository,
        pull_request: PullRequest,
        parsed_files: List[ParsedDiff],
        line_mapper: LineMapper,
        pr_metadata: Optional[Dict[str, Any]] = None,
        existing_comments: Optional[List[Dict[str, Any]]] = None,
        evidence: ChangedFileEvidenceReader | None = None,
        code_readers: tuple[CodeIndexReader | None, CodeIndexReader] | None = None,
        read_content=None,
        context_file_limit: int = 20,
        requirements: Optional[str] = None,
        knowledge: Optional[str] = None,
        impact: Optional[str] = None,
    ) -> CodeReview:
        """Generate review file by file for large diffs."""
        suggestion_filter = SuggestionFilter()
        all_suggestions = []

        for parsed_file in parsed_files:
            logger.info(f"Reviewing file: {parsed_file.file_path}")

            file_comments = None
            if existing_comments:
                file_comments = [
                    c
                    for c in existing_comments
                    if c.get("path") == parsed_file.file_path
                ]

            review_for_file = llm().generate_code_review(
                diff=parsed_file.diff_text,
                parsed_files=[parsed_file],
                pr_metadata=pr_metadata,
                existing_comments=file_comments or None,
                code_context=self._prepare_code_context(
                    code_readers,
                    repository.full_name or f"{repository.owner}/{repository.name}",
                    pull_request.head_sha,
                    [parsed_file.file_path],
                    read_content=read_content,
                    file_limit=context_file_limit,
                ),
                requirements=requirements,
                knowledge=knowledge,
                impact=impact,
            )

            if review_for_file and review_for_file.code_suggestions:
                accepted = self._process_suggestions(
                    review_for_file.code_suggestions,
                    suggestion_filter,
                    line_mapper,
                    evidence=evidence,
                )
                all_suggestions.extend(accepted)

        summary_obj = llm().generate_summary(all_suggestions)
        verdict = self._determine_verdict_from_suggestions(all_suggestions)

        return CodeReview(
            summary=summary_obj,
            verdict=verdict,
            code_suggestions=all_suggestions,
        )

    def _change_context_resolver(self, durable_code):
        try:
            return self.services.resolve(ChangeContextResolver)
        except LookupError:
            pass
        return DefaultChangeContextResolver(
            code=durable_code,
            knowledge=self._knowledge_selector(),
            requirements=self._requirement_selector(),
            impact=self._impact_preparer(),
        )

    def _knowledge_selector(self):
        try:
            return self.services.resolve(KnowledgeSelector)
        except LookupError:
            pass
        try:
            reader = self.services.resolve(KnowledgeReader)
        except LookupError:
            reader = _core_knowledge()
        return LinkedKnowledgeSelector(reader) if reader is not None else None

    def _requirement_selector(self):
        try:
            return self.services.resolve(RequirementSelector)
        except LookupError:
            pass
        try:
            reader = self.services.resolve(RequirementsReader)
        except LookupError:
            reader = _core_requirements()
        return LinkedRequirementSelector(reader) if reader is not None else None

    def _impact_preparer(self):
        try:
            return self.services.resolve(ChangeImpactResolver)
        except LookupError:
            return _core_impact_preparer(self.services)

    @staticmethod
    def _prepare_code_context(
        readers: tuple[CodeIndexReader | None, CodeIndexReader] | None,
        repository: str,
        revision: str,
        paths: List[str],
        *,
        read_content=None,
        file_limit: int = 20,
    ) -> str | None:
        if readers is None:
            return None
        contexts = []
        for reader in readers:
            if reader is None:
                continue
            try:
                context = DefaultReviewCodeContextPreparer(
                    reader,
                    read_content=read_content,
                    file_limit=file_limit,
                ).prepare(
                    repository=repository,
                    revision=revision,
                    paths=paths,
                )
            except (OSError, RuntimeError, ValueError):
                continue
            if context:
                contexts.append(context)
        merged = merge_review_code_contexts(contexts)
        return merged.content if merged else None

    def _process_suggestions(
        self,
        suggestions: List,
        suggestion_filter: SuggestionFilter,
        line_mapper: LineMapper,
        evidence: ChangedFileEvidenceReader | None = None,
        evidence_rejections: List[str] | None = None,
    ) -> List:
        """Filter and map suggestions to valid diff positions."""
        result = []
        validator = StructuralReviewEvidenceValidator()
        filtered, _ = suggestion_filter.filter_suggestions(suggestions)
        for suggestion in filtered:
            if line_mapper.suggestion_replays_diff(suggestion):
                logger.info(
                    f"Filtered out suggestion for "
                    f"{suggestion.file_name}:{suggestion.start_line}: "
                    f"suggested code is already applied"
                )
                continue
            mapped_result = line_mapper.validate_and_map_suggestion(
                suggestion, strict_mode=(APP_ENV == "production")
            )
            if mapped_result:
                mapping, reason = mapped_result
                suggestion.position = mapping.get("position")
                suggestion.end_line = mapping["line"]
                suggestion.side = Side(mapping["side"])
                if "start_line" in mapping:
                    suggestion.start_line = mapping["start_line"]
                decision = validator.validate(
                    suggestion.claims,
                    evidence.read(suggestion.file_name) if evidence else None,
                )
                if decision.contradicted:
                    if evidence_rejections is not None:
                        evidence_rejections.append(decision.reason)
                    logger.info(
                        f"Filtered contradicted suggestion for {suggestion.file_name}: "
                        f"{decision.reason}"
                    )
                    continue
                result.append(suggestion)
        return result

    @staticmethod
    def _summary_from_suggestions(suggestions: List) -> CodeReviewSummary:
        critical_categories = {SuggestionCategory.BUG, SuggestionCategory.SECURITY}
        critical = [
            suggestion.comment
            for suggestion in suggestions
            if suggestion.category in critical_categories
        ]
        minor = [
            suggestion.comment
            for suggestion in suggestions
            if suggestion.category not in critical_categories
        ]
        overview = (
            f"Review found {len(suggestions)} actionable issue(s)."
            if suggestions
            else "No actionable issues were found."
        )
        return CodeReviewSummary(
            overview=overview,
            key_improvements=[],
            minor_suggestions=minor,
            critical_issues=critical,
        )

    def _determine_verdict_from_suggestions(self, suggestions: List) -> Verdict:
        """Determine the appropriate verdict based on suggestions analysis."""
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

    @staticmethod
    def _filter_duplicate_suggestions(
        suggestions: List,
        existing_comments: List[Dict[str, Any]],
    ) -> List:
        """Remove suggestions that match already-posted bot comments."""
        filtered = []
        for suggestion in suggestions:
            if CodeReviewerPlugin._is_duplicate(suggestion, existing_comments):
                logger.info(
                    f"Filtering duplicate suggestion on {suggestion.file_name}:"
                    f"{suggestion.start_line}-{suggestion.end_line}"
                )
                continue
            filtered.append(suggestion)
        return filtered

    LINE_TOLERANCE = 3
    CODE_SIMILARITY_THRESHOLD = 85
    COMMENT_SIMILARITY_THRESHOLD = 70
    COMMENT_SIMILARITY_STRICT = 60

    @staticmethod
    def _extract_suggestion_code(body: str) -> Optional[str]:
        match = re.search(r"```suggestion\s*\n(.*?)```", body, re.DOTALL)
        return match.group(1).strip() if match else None

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    @staticmethod
    def _lines_overlap(
        s_start: int, s_end: int, ec_start: int, ec_end: int, tolerance: int = 0
    ) -> bool:
        return (s_start - tolerance) <= ec_end and (s_end + tolerance) >= ec_start

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """Return 0-100 similarity using the best of rapidfuzz and difflib."""
        if not a or not b:
            return 0.0
        rf_score = max(
            fuzz.ratio(a, b),
            fuzz.token_sort_ratio(a, b),
            fuzz.token_set_ratio(a, b),
        )
        dl_score = difflib.SequenceMatcher(None, a, b).ratio() * 100
        return max(rf_score, dl_score)

    @staticmethod
    def _is_duplicate(suggestion, existing_comments: List[Dict[str, Any]]) -> bool:
        for ec in existing_comments:
            if ec.get("path") != suggestion.file_name:
                continue

            ec_line = ec.get("line")
            ec_start = ec.get("start_line") or ec_line
            if not ec_line:
                continue

            s_start = suggestion.start_line
            s_end = suggestion.end_line

            exact_overlap = CodeReviewerPlugin._lines_overlap(
                s_start, s_end, ec_start, ec_line
            )
            fuzzy_overlap = CodeReviewerPlugin._lines_overlap(
                s_start,
                s_end,
                ec_start,
                ec_line,
                tolerance=CodeReviewerPlugin.LINE_TOLERANCE,
            )

            if not fuzzy_overlap:
                continue

            ec_body = ec.get("body", "")

            ec_code = CodeReviewerPlugin._extract_suggestion_code(ec_body)
            s_code = suggestion.suggested_code
            if ec_code and s_code:
                code_sim = CodeReviewerPlugin._text_similarity(
                    CodeReviewerPlugin._normalize(s_code),
                    CodeReviewerPlugin._normalize(ec_code),
                )
                if code_sim >= CodeReviewerPlugin.CODE_SIMILARITY_THRESHOLD:
                    return True

            s_comment = CodeReviewerPlugin._normalize(suggestion.comment or "")
            ec_comment = CodeReviewerPlugin._normalize(ec_body)
            comment_sim = CodeReviewerPlugin._text_similarity(s_comment, ec_comment)

            if (
                exact_overlap
                and comment_sim >= CodeReviewerPlugin.COMMENT_SIMILARITY_STRICT
            ):
                return True

            if comment_sim >= CodeReviewerPlugin.COMMENT_SIMILARITY_THRESHOLD:
                return True

        return False


def _requirements_section(known) -> Optional[str]:
    if known is None or not known.requirements:
        return None
    lines = [
        "## Requirements This Change Is Answerable To",
        "Judge the change against these as well as against how it is written.",
        "",
    ]
    for item in known.requirements:
        lines.append(f"- {item.id} ({item.status}): {item.summary}")
    lines.append("")
    return "\n".join(lines)


def _knowledge_section(known) -> Optional[str]:
    if known is None or not known.knowledge:
        return None
    lines = [
        "## Decisions And Rules Governing This Code",
        "Recorded by the team and still standing. A change that breaks one of "
        "these is a defect even when the code reads correctly.",
        "",
    ]
    for item in known.knowledge:
        lines.append(f"- {item.id} ({item.kind}, {item.status}): {item.summary}")
    lines.append("")
    return "\n".join(lines)


def _impact_section(known) -> Optional[str]:
    if known is None or known.impact is None or not known.impact.findings:
        return None
    lines = [
        "## What This Change Reaches",
        "Parts of the wider system that depend on what is being changed. A "
        "finding marked uncertain is a question to raise, not a fact to assert.",
        "",
    ]
    for finding in known.impact.findings:
        certainty = "certain" if finding.certain else "uncertain"
        reached = ", ".join(finding.topology_entity_ids)
        lines.append(f"- {finding.summary} ({certainty}, reaches {reached})")
    lines.append("")
    return "\n".join(lines)


def _core_impact_preparer(services):
    from src.config.db import get_engine
    from src.core.impact import (
        DefaultChangeImpactResolver,
        SQLCompatibilityCheckRepository,
        SQLImpactSeedRepository,
    )
    from src.core.topology import SQLTopologyRepository, TopologyReader

    engine = get_engine()
    if engine is None:
        return None
    try:
        topology = services.resolve(TopologyReader)
    except LookupError:
        topology = SQLTopologyRepository(engine)
    return DefaultChangeImpactResolver(
        seeds=SQLImpactSeedRepository(engine),
        topology=topology,
        compatibility=SQLCompatibilityCheckRepository(engine),
    )


def _core_knowledge():
    from src.config.db import get_engine
    from src.core.knowledge import SQLKnowledgeRepository

    engine = get_engine()
    return SQLKnowledgeRepository(engine) if engine is not None else None


def _core_requirements():
    from src.config.db import get_engine
    from src.core.requirements import SQLRequirementsRepository

    engine = get_engine()
    return SQLRequirementsRepository(engine) if engine is not None else None


def _core_code_index():
    from src.config.db import get_engine

    engine = get_engine()
    return SQLCodeIndexRepository(engine) if engine is not None else None
