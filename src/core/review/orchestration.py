from __future__ import annotations

from dataclasses import replace

from src.core.analysis import ERROR, Analysis, also_reported
from src.core.parallel import SharedReader, parallel_map
from src.core.review_coverage.models import Coverage
from src.models.code_review import (
    CodeReview,
    CodeSuggestion,
    Side,
    SuggestionCategory,
    Severity,
    Verdict,
    is_nitpick,
    severity_of,
    summary_from,
)

from .evaluation import DeterministicReviewEvaluator
from .execution import (
    EvaluationStatus,
    ParallelReviewPlan,
    ParticipantOutcome,
    ReviewEvaluation,
    ReviewExecution,
    ReviewInput,
)
from .fingerprint import of_words
from .interfaces import ReviewPlan
from .models import Told
from src.core.review_performance import stage, review_session, session_id
from src.core.review_trace import finding as trace_finding
from src.utils.logger import logger


def verified(finding, judgments):
    """The finding ranked by the premises its supporting evaluations verified.

    Supporters can disagree; the softest verified reading wins, so a finding
    is never ranked above what every supporter established.
    """
    readings = [
        type(finding).model_validate(
            {**finding.model_dump(mode="json"), **judgment.premises}
        )
        for judgment in judgments
        if judgment.status == EvaluationStatus.SUPPORTED and judgment.premises
    ]
    if not readings:
        return finding
    return max(readings, key=lambda one: list(Severity).index(severity_of(one)))


class ParallelReviewOrchestrator:
    def __init__(self, deterministic: DeterministicReviewEvaluator | None = None):
        self.deterministic = deterministic or DeterministicReviewEvaluator()

    @review_session
    def review(self, request: ReviewInput, plan: ReviewPlan) -> ReviewExecution:
        ParallelReviewPlan(
            tuple(plan.reviewers),
            tuple(plan.evaluators),
            plan.concurrency,
            plan.minimum_support,
            plan.maximum_rejections,
        )

        reader = SharedReader(request.read_content) if request.read_content else None
        if reader is not None:
            request = replace(request, read_content=reader)

        def read(task):
            participant, repetition = task
            identity = f"{participant.name}:{repetition}"
            try:
                options = dict(request.options)
                if participant.investigations:
                    investigation = participant.investigations[repetition - 1]
                    options["investigation"] = investigation
                    options["include_experts"] = False
                if (
                    participant.expert_repetitions is not None
                    and repetition > participant.expert_repetitions
                ):
                    options["include_experts"] = False
                if (
                    isinstance(options.get("coverage"), Coverage)
                    or participant.investigations
                ):
                    options["coverage"] = coverage_by_pass[identity]
                options["told"] = (
                    *options.get("told", ()),
                    Told(
                        "Independent review",
                        f"Reading {identity}. Independently inspect the change.",
                    ),
                )
                if participant.investigations:
                    options["told"] = (
                        *options["told"],
                        Told(investigation.name, investigation.question),
                    )
                if participant.focuses:
                    options["told"] = (
                        *options["told"],
                        Told(
                            "Review focus",
                            participant.focuses[
                                (repetition - 1) % len(participant.focuses)
                            ]
                            + " Use the supplied source as evidence. Report other concrete defects too; this focus does not restrict finding scope.",
                        ),
                    )
                if request.analysis is not None:
                    options["analysis"] = request.analysis
                with stage(
                    "discovery", participant=participant.name, repetition=repetition
                ):
                    answer = participant.reviewer.review(
                        request.changes,
                        provider=participant.provider,
                        read_content=request.read_content,
                        **options,
                    )
                if answer is not None and not isinstance(answer, CodeReview):
                    raise ValueError("A reviewer must return CodeReview or None")
                return ParticipantOutcome(participant.name, repetition, answer)
            except Exception as error:
                logger.warning(
                    "review_participant_failed session=%s stage=discovery participant=%s repetition=%s error=%s reason=%s",
                    session_id(),
                    participant.name,
                    repetition,
                    type(error).__name__,
                    str(error) if type(error) is ValueError else "",
                )
                return ParticipantOutcome(
                    participant.name, repetition, error=type(error).__name__
                )

        tasks = [
            (participant, repetition)
            for participant in plan.reviewers
            for repetition in range(1, participant.repetitions + 1)
        ]
        coverage_by_pass = {
            f"{participant.name}:{repetition}": Coverage()
            for participant, repetition in tasks
        }

        def discover(task):
            if task is None:
                try:
                    with stage("static-analysis"):
                        answer = self.deterministic.evaluate(
                            request, (), pass_id="deterministic:1"
                        )
                    if (
                        not isinstance(answer, ReviewEvaluation)
                        or answer.analysis is None
                    ):
                        raise ValueError(
                            "The deterministic evaluator must return analysis"
                        )
                    return ParticipantOutcome("deterministic", 1, answer)
                except Exception as error:
                    return ParticipantOutcome(
                        "deterministic", 1, error=type(error).__name__
                    )
            return read(task)

        outcomes = parallel_map(discover, [*tasks, None], plan.concurrency)
        if reader is not None:
            request = replace(
                request,
                source_context={**request.source_context, **reader.captured()},
            )
        coverage = request.options.get("coverage")
        if isinstance(coverage, Coverage):
            for collected in coverage_by_pass.values():
                coverage.merge(collected)
        reviews = tuple(outcomes[:-1])
        if request.require_complete_discovery and any(one.error for one in reviews):
            raise RuntimeError("A discovery participant failed")
        deterministic = outcomes[-1]
        analysis = (
            deterministic.result.analysis
            if isinstance(deterministic.result, ReviewEvaluation)
            else Analysis(unavailable=("deterministic",))
        )
        candidates, producers, seen = [], [], {}
        for outcome in reviews:
            if not isinstance(outcome.result, CodeReview):
                continue
            for finding in outcome.result.code_suggestions or ():
                key = (
                    finding.start_line,
                    finding.end_line,
                    finding.side,
                    of_words(finding.file_name, finding.comment),
                )
                identity = f"{outcome.participant}:{outcome.repetition}"
                if key in seen:
                    producers[seen[key]].append(identity)
                else:
                    seen[key] = len(candidates)
                    candidates.append(finding)
                    producers.append([identity])

        tool_candidates = {}
        for observation in analysis.findings:
            tool_candidates[len(candidates)] = observation
            candidates.append(
                CodeSuggestion(
                    file_name=observation.path,
                    start_line=observation.start_line,
                    end_line=observation.end_line,
                    side=Side.RIGHT,
                    comment=observation.rendered().lstrip("- "),
                    category=(
                        SuggestionCategory.BUG
                        if observation.severity == ERROR
                        else SuggestionCategory.IMPROVEMENT
                    ),
                    suggested_code="",
                )
            )
            producers.append([f"analysis:{observation.tool}"])

        evaluating = replace(request, analysis=analysis)

        def evaluate(task):
            participant, repetition = task
            try:
                with stage(
                    "evaluation", participant=participant.name, repetition=repetition
                ):
                    result = participant.evaluator.evaluate(
                        evaluating,
                        tuple(one.model_copy(deep=True) for one in candidates),
                        pass_id=f"{participant.name}:{repetition}",
                    )
                if (
                    not isinstance(result, ReviewEvaluation)
                    or result.analysis is not None
                ):
                    raise ValueError("An evaluator must return ReviewEvaluation")
                indices = [one.candidate for one in result.judgments]
                if len(indices) != len(set(indices)) or any(
                    index < 0 or index >= len(candidates) for index in indices
                ):
                    raise ValueError("An evaluator returned invalid candidate indices")
                if any(
                    not isinstance(one.status, EvaluationStatus)
                    or not one.reason.strip()
                    or (one.status != EvaluationStatus.UNRESOLVED and not one.evidence)
                    for one in result.judgments
                ):
                    raise ValueError(
                        "An evaluator returned a judgment without evidence"
                    )
                if any(
                    one.duplicate_of is not None
                    and (
                        type(one.duplicate_of) is not int
                        or one.status != EvaluationStatus.SUPPORTED
                        or not 0 <= one.duplicate_of < one.candidate
                    )
                    for one in result.judgments
                ):
                    raise ValueError("An evaluator returned an invalid duplicate")
                return ParticipantOutcome(participant.name, repetition, result)
            except Exception as error:
                logger.warning(
                    "review_participant_failed session=%s stage=evaluation participant=%s repetition=%s error=%s reason=%s",
                    session_id(),
                    participant.name,
                    repetition,
                    type(error).__name__,
                    str(error) if type(error) is ValueError else "",
                )
                return ParticipantOutcome(
                    participant.name, repetition, error=type(error).__name__
                )

        evaluations = (
            deterministic,
            *parallel_map(
                evaluate,
                [
                    (participant, repetition)
                    for participant in plan.evaluators
                    for repetition in range(1, participant.repetitions + 1)
                ],
                plan.concurrency,
            ),
        )
        accepted = []
        investigation_gaps = tuple(
            dict.fromkeys(
                attempt.reason
                for collected in coverage_by_pass.values()
                for attempt in collected.attempts
                if attempt.question == "investigation" and not attempt.answered
            )
        )
        judgments = {}
        ranked = {}
        unresolved = False
        include_nitpicks = (
            request.changes.configuration.value("review.include_nitpicks") is True
        )
        for index, finding in enumerate(candidates):
            valid_location = 1 <= finding.start_line <= finding.end_line
            if (
                valid_location
                and finding.side == Side.RIGHT
                and request.read_content is not None
            ):
                try:
                    content = request.read_content(finding.file_name)
                    valid_location = isinstance(
                        content, str
                    ) and finding.end_line <= len(content.splitlines())
                except Exception:
                    valid_location = False
            judgments[index] = [
                judgment
                for outcome in evaluations
                if isinstance(outcome.result, ReviewEvaluation)
                for judgment in outcome.result.judgments
                if judgment.candidate == index
            ]
            votes = [one.status for one in judgments[index]]
            tally = ", ".join(
                f"{status.value} {votes.count(status)}" for status in EvaluationStatus
            )
            if getattr(finding, "doubt", None):
                tally = "doubted; " + tally
            if not valid_location:
                unresolved = True
                trace_finding(
                    "acceptance", "dropped", finding, reason="invalid location"
                )
                continue
            required = plan.minimum_support
            if getattr(finding, "doubt", None):
                required = max(required, 1)
            if (
                votes.count(EvaluationStatus.SUPPORTED) >= required
                and votes.count(EvaluationStatus.REJECTED) <= plan.maximum_rejections
            ):
                ranked[index] = verified(finding, judgments[index])
                if (
                    not include_nitpicks
                    and ranked[index] is not finding
                    and is_nitpick(ranked[index])
                ):
                    trace_finding(
                        "acceptance",
                        "dropped",
                        finding,
                        reason="verified premises make it a nitpick; " + tally,
                    )
                    continue
                accepted.append(index)
                trace_finding("acceptance", "accepted", finding, reason=tally)
                continue
            if votes.count(EvaluationStatus.REJECTED) <= plan.maximum_rejections and (
                not votes or EvaluationStatus.UNRESOLVED in votes
            ):
                unresolved = True
            trace_finding("acceptance", "dropped", finding, reason=tally)
        grouped = {}
        canonical = {}
        representatives = {}
        for index in accepted:
            links = [
                one.duplicate_of
                for one in judgments[index]
                if one.status == EvaluationStatus.SUPPORTED
            ]
            target = index
            if (
                len(links) >= max(1, plan.minimum_support)
                and links[0] is not None
                and all(one == links[0] for one in links)
            ):
                target = canonical.get(
                    links[0], representatives.setdefault(links[0], index)
                )
            canonical[index] = target
            grouped.setdefault(target, []).append(index)
            if target != index:
                trace_finding(
                    "grouping",
                    "merged",
                    candidates[index],
                    reason=f"duplicate of candidate {target}",
                )
        retained = [ranked[index] for index in grouped if index not in tool_candidates]
        retained_tools = [
            tool_candidates[index] for index in grouped if index in tool_candidates
        ]
        blocking = any(
            severity_of(ranked[index]) == Severity.BLOCKING
            or (index in tool_candidates and tool_candidates[index].severity == ERROR)
            for index in accepted
        )
        analysis = replace(analysis, findings=tuple(retained_tools))
        completed = any(isinstance(one.result, CodeReview) for one in reviews)
        review = None
        if completed:
            incomplete = (
                any(one.error or one.result is None for one in reviews)
                or any(one.error for one in evaluations)
                or bool(analysis is not None and analysis.unavailable)
                or bool(analysis is not None and analysis.unchecked)
                or unresolved
                or bool(investigation_gaps)
            )
            verdict = (
                Verdict.REQUEST_CHANGES
                if blocking
                else Verdict.COMMENT if retained or incomplete else Verdict.APPROVE
            )
            review = also_reported(
                CodeReview(
                    verdict=verdict,
                    code_suggestions=retained,
                    summary=summary_from(retained),
                ),
                analysis,
            )
            if incomplete:
                review.summary.minor_suggestions.append(
                    "Review coverage is incomplete. Inspect the execution details before approving."
                )
        return ReviewExecution(
            review,
            tuple(candidates),
            tuple(tuple(one) for one in producers),
            reviews,
            tuple(evaluations),
            analysis,
            tuple(tuple(one) for one in grouped.values()),
            investigation_gaps,
        )
