"""The reviewer, whatever the change came from.

Callers supply the model, how to read a file at the revision under review, and
what has already been said about it. The index, recorded decisions, what the
change reaches and the evidence claims are checked against are assembled here.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, List, Sequence

from rapidfuzz import fuzz

from src.core.parallel import parallel_map
from src.core.review.exclusions import review_diff
from src.core.skills.matching import matches
from src.core.skills.selection import for_review
from src.core.analysis import Analysis, also_reported
from src.core.change_context import ChangeSet
from src.core.code_index import CodeIndexReader
from src.plugins.builtin.code_reviewer.context import (
    durable_index,
    impact_section,
    knowledge_section,
    known_for,
    prepare_code_context,
    related_code_section,
    requirements_section,
)
from src.core.review_context import LazyChangedFileCodeIndex
from src.core.review.stopping import abandoned as given_up
from src.core.review_coverage import Coverage, SKILLS
from src.core.review_evidence import (
    CachedChangedFileEvidenceReader,
    ChangedFileEvidenceReader,
    FallbackChangedFileEvidenceReader,
    IndexedChangedFileEvidenceReader,
    StructuralReviewEvidenceValidator,
    claimed_absent,
)
from src.core.scope import Scope
from src.core.skills import (
    Change,
    LLMSkillChecker,
    Skill,
    SkillSource,
    SkillType,
    split,
)
from src.core.review.fingerprint import of_code, of_words
from src.core.review.models import Sections, Told
from src.core.services import ServiceRegistry, service_registry
from src.core.settings.configuration import Configuration
from src.models.code_review import (
    CodeReview,
    CodeReviewSummary,
    is_nitpick,
    severity_of,
    summary_from,
    Severity,
    Side,
    Verdict,
)
from src.utils.diff_parser import ParsedDiff, parse_diff
from src.utils.line_mapper import LineMapper
from src.utils.logger import logger
from src.utils.suggestion_filter import SuggestionFilter

# How alike two statements of a problem have to be before they are one
# problem. Set where a reworded version of the same complaint matches and a
# different complaint about the same line does not.
SAME_ISSUE = 70


def _says_the_same(said: str, reported: str) -> bool:
    """Whether a review is making the point a tool already made."""
    if not said or not reported:
        return False
    return (
        max(
            fuzz.token_set_ratio(said, reported),
            fuzz.partial_ratio(said, reported),
        )
        >= SAME_ISSUE
    )


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


def _stopped(changes, metadata, revision: str) -> bool:
    """Whether this review is no longer wanted."""
    return given_up(
        str(changes.scope.get("repository") or ""),
        int((metadata or {}).get("number") or 0),
        revision,
    )


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
        previous_summary: str | None = None,
        told: Sequence[Told] = (),
        skills: Sequence[Skill] = (),
        expert_passes: str | None = None,
        code_scope: Scope | None = None,
        metadata: dict | None = None,
        coverage: Coverage | None = None,
        analysis: Analysis | None = None,
        revision: str = "",
    ) -> CodeReview | None:
        """The review, or None where there was nothing to read.

        `revision` is the commit being read. Where another revision has since
        become the current one, the review is stopped.

        None also where the review was stopped: a caller cannot tell the two
        apart from the return alone.

        A caller that passes a coverage record gets back what this was able
        to read written into it, which is the only way to tell a review that
        found nothing from one that could not look.
        """
        if _stopped(changes, metadata, revision):
            return None
        coverage = coverage if coverage is not None else Coverage()
        filtered_diff, omitted = review_diff(changes.diff, changes.configuration)
        for path in omitted:
            coverage.record(
                "changed file",
                "exclusion pattern",
                answered=False,
                target=path,
                reason="excluded by review.exclude_patterns",
            )
        if omitted and not filtered_diff.strip():
            return CodeReview(
                summary=CodeReviewSummary(
                    overview="All changed files are excluded from review.",
                    minor_suggestions=[],
                    critical_issues=[],
                ),
                verdict=Verdict.COMMENT,
                code_suggestions=[],
            )
        changes = replace(
            changes,
            diff=filtered_diff,
            files=tuple(f for f in changes.files if f.path not in omitted),
        )
        parsed_files = parse_diff(changes.diff)
        if not parsed_files:
            return None
        line_mapper = LineMapper(parsed_files)
        readable = [
            parsed_file.file_path
            for parsed_file in parsed_files
            if not parsed_file.is_binary_file and parsed_file.file_path
        ]

        configuration = changes.configuration
        include_nitpicks = configuration.value("review.include_nitpicks") is True
        file_limit = (
            configuration.value("review.structural_context_file_limit")
            or DEFAULT_FILE_LIMIT
        )

        durable_code, local_code = self._indexes(
            changes, readable, read_content, file_limit, code_scope
        )
        known = known_for(changes, self.services, durable_code, coverage)

        sections = Sections(
            requirements=requirements_section(known),
            knowledge=self._joined(
                [knowledge_section(known), *(one.rendered() for one in told)]
            ),
            impact=impact_section(known),
            related_code=related_code_section(
                changes,
                self.services,
                durable_code,
                read_content,
                code_scope,
                known,
                coverage,
                provider,
            ),
            analysis=analysis.rendered() if analysis else None,
        )

        if not include_nitpicks:
            sections = replace(
                sections,
                knowledge=self._joined(
                    [
                        sections.knowledge,
                        Told(
                            "Finding policy",
                            "Nitpicks are disabled. Report only concrete bugs, security "
                            "issues, or material performance problems supported by the "
                            "changed code. Omit style, naming, clarity, documentation, "
                            "refactoring, and optional improvements from findings and "
                            "overview sections. Do not relabel cosmetic advice as a bug "
                            "or performance issue. State the failing scenario and its "
                            "impact; speculative benefits or tiny optimizations do not "
                            "qualify. This policy applies to every expert pass too.",
                        ).rendered(),
                    ]
                ),
            )

        evidence = self._evidence(changes, durable_code, read_content, code_scope)
        metadata = metadata or self._metadata(changes)

        readers = (durable_code, local_code)
        budget = self._budget(configuration)

        available = {skill.id: skill for skill in skills}
        for source in self.services.contributions(SkillSource):
            for skill in source.read():
                available[skill.id] = skill
        chosen = for_review(
            tuple(available.values()),
            Change(changes.title, changes.description, changes.paths, changes.diff),
            (
                configuration.value("review.expert_passes")
                if expert_passes is None
                else expert_passes
            ),
        )
        # Most skills are a pointer at the page that holds the rule, and a
        # change judged against a pointer is judged against nothing.
        selected = split(chosen)
        applied = {skill.id for skill in chosen}
        for skill in selected:
            coverage.record(SKILLS, skill.kind, answered=True, target=skill.id)
        # A skill that matched and did not fit leaves no trace anywhere else.
        for skill in available.values():
            if skill.id not in applied:
                coverage.record(
                    SKILLS,
                    skill.kind,
                    answered=False,
                    target=skill.id,
                    reason="did not apply to this change",
                )
        guidance = [
            skill
            for skill in selected
            if skill.kind not in {SkillType.REVIEW_PASS, SkillType.INITIALIZATION_PASS}
        ]
        if guidance:
            sections = replace(
                sections,
                knowledge=self._joined(
                    [
                        sections.knowledge,
                        *(
                            Told(skill.name, skill.body).rendered()
                            for skill in guidance
                        ),
                    ]
                ),
            )

        def read(pass_sections, paths=()):
            pass_files = [
                one
                for one in parsed_files
                if not paths or matches(one.file_path, paths)
            ]
            if not pass_files:
                return None
            pass_changes = replace(
                changes, diff="\n".join(one.diff_text for one in pass_files)
            )
            total = sum(provider.count_tokens(one.diff_text) for one in pass_files)
            if total <= budget:
                logger.info("The whole change fits in one reading.")
                return self._in_one_pass(
                    provider,
                    pass_changes,
                    pass_files,
                    line_mapper,
                    readers,
                    evidence,
                    metadata,
                    existing_comments,
                    previous_summary,
                    pass_sections,
                    read_content,
                    file_limit,
                    code_scope,
                )

            batches = _batched(pass_files, budget, provider.count_tokens)
            logger.info(f"Reading {len(pass_files)} files in {len(batches)} passes.")
            return self._in_batches(
                provider,
                pass_changes,
                pass_files,
                batches,
                line_mapper,
                readers,
                evidence,
                metadata,
                existing_comments,
                previous_summary,
                pass_sections,
                read_content,
                file_limit,
                code_scope,
                revision=revision,
            )

        focused = [skill for skill in selected if skill.kind == SkillType.REVIEW_PASS]

        def run_pass(skill):
            if skill is None:
                return read(sections)
            instructions = (
                "Review only the concern defined by this skill. Return the existing "
                "structured review format. Report only findings supported by the "
                "supplied source and diff, including whether an apparent conflict "
                "has already been removed.\n\n" + skill.body
            )
            try:
                return read(
                    replace(
                        sections,
                        knowledge=self._joined(
                            [
                                sections.knowledge,
                                Told(skill.name, instructions).rendered(),
                            ]
                        ),
                    ),
                    skill.paths,
                )
            except Exception as error:  # noqa: BLE001 - one pass, not the review
                # One pass is one extra reading. Losing it costs what it
                # would have said; losing the review costs every other pass.
                logger.warning(
                    "The %s pass did not finish: %s", skill.id, error, exc_info=True
                )
                coverage.record(
                    SKILLS,
                    skill.kind,
                    answered=False,
                    target=skill.id,
                    reason=f"the pass did not finish: {type(error).__name__}",
                )
                return None

        answers = parallel_map(run_pass, [None, *focused])
        answer = answers[0]
        if answer is None:
            return None
        combined = [
            finding
            for result in answers
            if result
            for finding in (result.code_suggestions or ())
        ]
        self._check_they_were_applied(
            provider, changes, configuration, guidance, coverage
        )

        unique, seen = [], set()
        for suggestion in self._beyond(analysis, combined):
            if not include_nitpicks and is_nitpick(suggestion):
                continue
            anchor = (suggestion.start_line, suggestion.end_line, suggestion.side)
            keys = {(*anchor, of_words(suggestion.file_name, suggestion.comment))}
            if suggestion.suggested_code:
                keys.add(
                    (*anchor, of_code(suggestion.file_name, suggestion.suggested_code))
                )
            if not seen.intersection(keys):
                unique.append(suggestion)
                seen.update(keys)
        # What the tools found goes out with the review. Findings are dropped
        # for repeating a tool, so the tool has to be the thing that said it.
        review = CodeReview(
            summary=summary_from(unique, answer.summary),
            verdict=verdict_from(unique),
            code_suggestions=unique,
            scores=answer.scores,
        )
        return also_reported(review, analysis)

    @staticmethod
    def _beyond(analysis, suggestions):
        """Whatever the review said that a tool had not already said.

        Sameness is judged on what was said, not on where it was said. Two
        problems live on one line often enough that dropping a finding for
        sharing a line with a linter hit would lose real ones, and a lost
        finding is worse than a repeated one.
        """
        if not analysis or not analysis.findings:
            return suggestions
        kept, covered = [], 0
        for suggestion in suggestions:
            here = analysis.covering(
                suggestion.file_name, suggestion.start_line, suggestion.end_line
            )
            if any(_says_the_same(suggestion.comment, one.message) for one in here):
                covered += 1
                continue
            kept.append(suggestion)
        if covered:
            logger.info(f"Dropped {covered} findings a tool had already reported")
        return kept

    @staticmethod
    def _check_they_were_applied(provider, changes, configuration, guidance, coverage):
        """Ask whether the review actually judged the change against each skill.

        A skill attached to a prompt and a skill a review took notice of look
        identical from here, and only one of them is worth having. What comes
        back is recorded and never blocks: a skill the review missed is a gap
        in the review rather than a fault in the change.

        A model call per skill on every pull request, so it waits to be asked
        for.
        """
        if not guidance or not configuration.value("review.check_skills_were_applied"):
            for skill in guidance:
                coverage.record(
                    SKILLS,
                    "honoured",
                    answered=False,
                    target=skill.id,
                    reason="not checked",
                )
            return
        subject = Change(
            title=changes.title,
            description=changes.description,
            paths=changes.paths,
            diff=changes.diff,
        )
        checker = LLMSkillChecker(ask=provider.generate_text, model=provider.model)

        def asked(skill):
            """One skill's answer, or why there is none.

            A check that fails takes itself down. The review it is asking
            about is already written, and losing that to a provider timing
            out would trade the whole reading for one question about it.
            """
            try:
                return skill, checker.check(skill, subject), None
            except Exception as error:  # noqa: BLE001 - one check, not the review
                return skill, None, error

        # Asked in turn, five skills is a minute, which is long enough for
        # something in between to time out.
        with ThreadPoolExecutor(max_workers=min(len(guidance), MAX_AT_ONCE)) as pool:
            answers = list(pool.map(asked, guidance))

        for skill, verdict, error in answers:
            if error is not None:
                logger.warning(
                    "Could not check %s was applied: %s", skill.id, error, exc_info=True
                )
                coverage.record(
                    SKILLS,
                    "honoured",
                    answered=False,
                    target=skill.id,
                    reason=f"the check did not finish: {type(error).__name__}",
                )
                continue
            coverage.record(
                SKILLS,
                "honoured",
                answered=verdict.passed,
                target=skill.id,
                reason="" if verdict.passed else (verdict.note or "not applied"),
            )

    @staticmethod
    def _budget(configuration: Configuration) -> int:
        """Where a review stops finding things, which is far below the window."""
        stated = configuration.value("review.reading_budget")
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
        previous_summary,
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
            previous_summary=previous_summary,
            code_context=code_context,
            requirements=sections.requirements,
            knowledge=sections.knowledge,
            impact=sections.impact,
            related_code=sections.related_code,
            analysis=sections.analysis,
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
                summary=summary_from(suggestions, full_review.summary),
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
        previous_summary,
        sections,
        read_content,
        file_limit,
        code_scope,
        revision="",
    ) -> CodeReview | None:
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
            if _stopped(changes, metadata, revision):
                return None
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
                previous_summary=previous_summary,
                code_context=self._context(
                    changes,
                    readers,
                    paths,
                    read_content,
                    file_limit,
                    code_scope,
                ),
                requirements=sections.requirements,
                knowledge=sections.knowledge,
                impact=sections.impact,
                related_code=sections.related_code,
                analysis=sections.analysis,
            )

        answers = parallel_map(read, batches, MAX_AT_ONCE)

        if _stopped(changes, metadata, revision):
            return None

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
            summary=summary_from(suggestions),
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
                suggestion, strict_mode=True
            )
            if suggestion.comment_only and not mapped_result:
                logger.info(
                    "Filtered comment-only finding without a valid diff anchor for %s",
                    suggestion.file_name,
                )
                continue
            if mapped_result:
                mapping, reason = mapped_result
                suggestion.position = mapping.get("position")
                suggestion.end_line = mapping["line"]
                suggestion.side = Side(mapping["side"])
                if "start_line" in mapping:
                    suggestion.start_line = mapping["start_line"]
            decision = validator.validate(
                list(suggestion.claims) + list(claimed_absent(suggestion.comment)),
                evidence.read(suggestion.file_name) if evidence else None,
                at=suggestion.start_line,
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


def verdict_from(suggestions: List) -> Verdict:
    """The verdict the suggestions add up to, not the one the model claimed.

    A model reporting bugs and approving anyway has contradicted itself; the
    suggestions are the part with evidence behind them.
    """
    if not suggestions:
        return Verdict.APPROVE

    blocking = any(
        suggestion
        and suggestion.comment
        and severity_of(suggestion) is Severity.BLOCKING
        for suggestion in suggestions
    )

    return Verdict.REQUEST_CHANGES if blocking else Verdict.COMMENT
