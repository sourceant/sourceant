"""The reviewer, whatever the change came from.

Callers supply the model, how to read a file at the revision under review, and
what has already been said about it. The index, recorded decisions, what the
change reaches and the evidence claims are checked against are assembled here.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, List, Sequence

from src.config.settings import APP_ENV
from src.core.change_context import ChangeSet
from src.core.code_index import CodeIndexReader
from src.plugins.builtin.code_reviewer.context import (
    durable_index,
    impact_section,
    knowledge_section,
    known_for,
    prepare_code_context,
    requirements_section,
)
from src.core.review_context import LazyChangedFileCodeIndex
from src.core.review_evidence import (
    CachedChangedFileEvidenceReader,
    ChangedFileEvidenceReader,
    FallbackChangedFileEvidenceReader,
    IndexedChangedFileEvidenceReader,
    StructuralReviewEvidenceValidator,
)
from src.core.scope import Scope
from src.core.review.models import Sections, Told
from src.core.services import ServiceRegistry, service_registry
from src.core.settings.resolver import value_of
from src.models.code_review import (
    CodeReview,
    CodeReviewSummary,
    Side,
    SuggestionCategory,
    Verdict,
)
from src.utils.diff_parser import ParsedDiff, parse_diff
from src.utils.line_mapper import LineMapper
from src.utils.logger import logger
from src.utils.suggestion_filter import SuggestionFilter

DEFAULT_FILE_LIMIT = 20

# How much is worth reading in one go, which is not how much fits.
DEFAULT_READING_BUDGET = 15_000

# Short enough a wait, without asking a provider for everything at once.
MAX_AT_ONCE = 6


def _batched(parsed_files, budget: int, cost) -> list[list]:
    """The change in parts, each within the budget.

    Order is kept, so a directory travels together. A file larger than the
    whole budget goes alone rather than being dropped.
    """
    batches: list[list] = []
    current: list = []
    running = 0
    for one in parsed_files:
        size = cost(one.diff_text)
        if current and running + size > budget:
            batches.append(current)
            current, running = [], 0
        current.append(one)
        running += size
    if current:
        batches.append(current)
    return batches


@dataclass
class CodeReviewer:
    """Registered against core's Reviewer interface.

    The model arrives per call, not per instance: a personal deployment's
    changes the moment its key is edited.
    """

    services: ServiceRegistry = field(default=service_registry)

    def review(
        self,
        changes: ChangeSet,
        *,
        provider: Any,
        read_content: Callable[[str], str | None] | None = None,
        existing_comments: Sequence[dict] | None = None,
        told: Sequence[Told] = (),
        code_scope: Scope | None = None,
        metadata: dict | None = None,
    ) -> CodeReview | None:
        """The review, or None where there was nothing to read."""
        parsed_files = parse_diff(changes.diff)
        if not parsed_files:
            return None

        line_mapper = LineMapper(parsed_files)
        readable = [
            parsed_file.file_path
            for parsed_file in parsed_files
            if not parsed_file.is_binary_file and parsed_file.file_path
        ]

        repository = str(changes.scope.get("repository") or "")
        file_limit = (
            value_of("review.structural_context_file_limit", repository=repository)
            or DEFAULT_FILE_LIMIT
        )

        durable_code, local_code = self._indexes(
            changes, readable, read_content, file_limit, code_scope
        )
        known = known_for(changes, self.services, durable_code)

        sections = Sections(
            requirements=requirements_section(known),
            knowledge=self._joined(
                [knowledge_section(known), *(one.rendered() for one in told)]
            ),
            impact=impact_section(known),
        )

        evidence = self._evidence(changes, durable_code, read_content, code_scope)
        metadata = metadata or self._metadata(changes)

        readers = (durable_code, local_code)
        budget = self._budget(repository)
        total = sum(provider.count_tokens(one.diff_text) for one in parsed_files)

        if total <= budget:
            logger.info("The whole change fits in one reading.")
            return self._in_one_pass(
                provider,
                changes,
                parsed_files,
                line_mapper,
                readers,
                evidence,
                metadata,
                existing_comments,
                sections,
                read_content,
                file_limit,
                code_scope,
            )

        batches = _batched(parsed_files, budget, provider.count_tokens)
        logger.info(f"Reading {len(parsed_files)} files in {len(batches)} passes.")
        return self._in_batches(
            provider,
            changes,
            parsed_files,
            batches,
            line_mapper,
            readers,
            evidence,
            metadata,
            existing_comments,
            sections,
            read_content,
            file_limit,
            code_scope,
        )

    @staticmethod
    def _budget(repository: str) -> int:
        """Where a review stops finding things, which is far below the window."""
        stated = value_of("review.reading_budget", repository=repository)
        try:
            return int(stated) if stated else DEFAULT_READING_BUDGET
        except (TypeError, ValueError):
            return DEFAULT_READING_BUDGET

    # ---------------------------------------------------------------- parts --

    def _indexes(self, changes, readable, read_content, file_limit, code_scope):
        """The stored index, and one built over the changed files."""
        scope = code_scope or changes.code_scope
        durable_code = durable_index(self.services)
        local_code = None
        if read_content is not None and readable:
            local_code = LazyChangedFileCodeIndex(
                scope, readable, read_content, file_limit=file_limit
            )
        return durable_code, local_code

    def _evidence(
        self, changes, durable_code, read_content, code_scope
    ) -> ChangedFileEvidenceReader | None:
        """What a claim is checked against before it is reported."""
        if read_content is None:
            return None
        local = CachedChangedFileEvidenceReader(read_content)
        if durable_code is None:
            return local
        return FallbackChangedFileEvidenceReader(
            IndexedChangedFileEvidenceReader(
                durable_code, code_scope or changes.code_scope
            ),
            local,
        )

    @staticmethod
    def _metadata(changes: ChangeSet) -> dict:
        return {
            "title": changes.title,
            "description": changes.description,
        }

    def _context(self, changes, readers, paths, read_content, file_limit, code_scope):
        return prepare_code_context(
            readers,
            str(changes.scope.get("repository") or ""),
            changes.revision,
            paths,
            scope=code_scope,
            read_content=read_content,
            file_limit=file_limit,
        )

    def _in_one_pass(
        self,
        provider,
        changes,
        parsed_files,
        line_mapper,
        readers,
        evidence,
        metadata,
        existing_comments,
        sections,
        read_content,
        file_limit,
        code_scope,
    ) -> CodeReview:
        suggestion_filter = SuggestionFilter()
        code_context = self._context(
            changes,
            readers,
            [one.file_path for one in parsed_files],
            read_content,
            file_limit,
            code_scope,
        )

        full_review = provider.generate_code_review(
            diff=changes.diff,
            parsed_files=parsed_files,
            pr_metadata=metadata,
            existing_comments=list(existing_comments or []) or None,
            code_context=code_context,
            requirements=sections.requirements,
            knowledge=sections.knowledge,
            impact=sections.impact,
        )

        suggestions: List = []
        rejections: List[str] = []
        if full_review and full_review.code_suggestions:
            suggestions = self.process(
                full_review.code_suggestions,
                suggestion_filter,
                line_mapper,
                evidence=evidence,
                evidence_rejections=rejections,
            )

        verdict = verdict_from(suggestions)
        if full_review:
            return CodeReview(
                summary=(
                    summary_from(suggestions) if rejections else full_review.summary
                ),
                verdict=verdict,
                code_suggestions=suggestions,
                scores=full_review.scores,
            )
        return CodeReview(summary=None, verdict=verdict, code_suggestions=suggestions)

    def _in_batches(
        self,
        provider,
        changes,
        parsed_files,
        batches,
        line_mapper,
        readers,
        evidence,
        metadata,
        existing_comments,
        sections,
        read_content,
        file_limit,
        code_scope,
    ) -> CodeReview:
        """Read the change in parts and put what each said together.

        Concurrently: the parts do not depend on each other, and in turn they
        are a round trip each.
        """
        suggestion_filter = SuggestionFilter()
        suggestions: List = []
        # Carried across batches, so the summary correction is not limited to
        # a change that fitted in one reading.
        rejections: List[str] = []

        def read(batch):
            paths = [one.file_path for one in batch]
            about_these = None
            if existing_comments:
                about_these = [
                    one for one in existing_comments if one.get("path") in set(paths)
                ]
            return provider.generate_code_review(
                diff="\n".join(one.diff_text for one in batch),
                parsed_files=batch,
                pr_metadata=metadata,
                existing_comments=about_these or None,
                code_context=self._context(
                    changes, readers, paths, read_content, file_limit, code_scope
                ),
                requirements=sections.requirements,
                knowledge=sections.knowledge,
                impact=sections.impact,
            )

        with ThreadPoolExecutor(max_workers=min(len(batches), MAX_AT_ONCE)) as pool:
            answers = list(pool.map(read, batches))

        for answer in answers:
            if answer and answer.code_suggestions:
                suggestions.extend(
                    self.process(
                        answer.code_suggestions,
                        suggestion_filter,
                        line_mapper,
                        evidence=evidence,
                        evidence_rejections=rejections,
                    )
                )

        return CodeReview(
            summary=(
                summary_from(suggestions)
                if rejections
                else provider.generate_summary(suggestions)
            ),
            verdict=verdict_from(suggestions),
            code_suggestions=suggestions,
        )

    @staticmethod
    def _joined(sections) -> str | None:
        """The prompt's one free-form slot."""
        written = [one for one in sections if one]
        return "\n".join(written) if written else None

    def process(
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


def summary_from(suggestions: List) -> CodeReviewSummary:
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


def verdict_from(suggestions: List) -> Verdict:
    """The verdict the suggestions add up to, not the one the model claimed.

    A model reporting bugs and approving anyway has contradicted itself; the
    suggestions are the part with evidence behind them.
    """
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

    return Verdict.REQUEST_CHANGES if critical_count else Verdict.COMMENT
