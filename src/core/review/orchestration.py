from __future__ import annotations

from dataclasses import replace

from src.core.analysis import ERROR, Analysis, also_reported
from src.core.parallel import parallel_map
from src.core.review_coverage.models import Coverage
from src.models.code_review import (
    CodeReview,
    Severity,
    Verdict,
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


class ParallelReviewOrchestrator:
    def __init__(self, deterministic: DeterministicReviewEvaluator | None = None):
        self.deterministic = deterministic or DeterministicReviewEvaluator()

    def review(self, request: ReviewInput, plan: ReviewPlan) -> ReviewExecution:
        ParallelReviewPlan(
            tuple(plan.reviewers),
            tuple(plan.evaluators),
            plan.concurrency,
            plan.minimum_support,
            plan.maximum_rejections,
        )

        def read(task):
            participant, repetition = task
            identity = f"{participant.name}:{repetition}"
            try:
                options = dict(request.options)
                if isinstance(options.get("coverage"), Coverage):
                    options["coverage"] = coverage_by_pass[identity]
                options["told"] = (
                    *options.get("told", ()),
                    Told(
                        "Independent review",
                        f"Reading {identity}. Independently inspect the change.",
                    ),
                )
                if request.analysis is not None:
                    options["analysis"] = request.analysis
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
        coverage = request.options.get("coverage")
        if isinstance(coverage, Coverage):
            for collected in coverage_by_pass.values():
                coverage.merge(collected)
        reviews = tuple(outcomes[:-1])
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

        evaluating = replace(request, analysis=analysis)

        def evaluate(task):
            participant, repetition = task
            try:
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
                return ParticipantOutcome(participant.name, repetition, result)
            except Exception as error:
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
        retained = []
        for index, finding in enumerate(candidates):
            votes = [
                judgment.status
                for outcome in evaluations
                if isinstance(outcome.result, ReviewEvaluation)
                for judgment in outcome.result.judgments
                if judgment.candidate == index
            ]
            if (
                votes.count(EvaluationStatus.SUPPORTED) >= plan.minimum_support
                and votes.count(EvaluationStatus.REJECTED) <= plan.maximum_rejections
            ):
                retained.append(finding)
        completed = any(isinstance(one.result, CodeReview) for one in reviews)
        review = None
        if completed:
            incomplete = (
                any(one.error or one.result is None for one in reviews)
                or any(one.error for one in evaluations)
                or bool(analysis is not None and analysis.unavailable)
                or bool(analysis is not None and analysis.unchecked)
                or bool(candidates and not retained)
            )
            verdict = (
                Verdict.REQUEST_CHANGES
                if (
                    any(severity_of(one) == Severity.BLOCKING for one in retained)
                    or (analysis is not None and analysis.counted(ERROR) > 0)
                )
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
        )
