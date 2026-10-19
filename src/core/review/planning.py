from __future__ import annotations

from src.core.model import ModelRouter, providers_for
from src.core.services import ServiceRegistry, service_registry
from .evaluation import ModelReviewEvaluator
from .interfaces import ReviewInvestigationSource
from .investigations import ChangedBehaviorInvestigationSource
from .execution import (
    EvaluationParticipant,
    ParallelReviewPlan,
    ReviewInput,
    ReviewParticipant,
)

READING_FOCUSES = (
    "Inspect the whole change for concrete defects across behavior, security, contracts, tests and documentation.",
    "Trace changed behavior through callers, supported runtimes and dependencies. Check compatibility, failures, resource ownership and error reporting.",
    "Check each changed behavior against explicit repository requirements, one requirement at a time. Trace invalid input through its real caller and error handler. Compare new documentation and changelog claims with what the implementation actually guarantees; a newly false claim is distinct from a pre-existing implementation limitation. Check negative tests for newly introduced branches. Report each concrete contract violation separately with its own useful fix.",
)


def evaluated(configuration) -> bool:
    """Whether every discovered finding must win a supporting evaluation."""
    return (
        configuration.value("review.plan") == "premium"
        and configuration.value("review.evaluation_passes") > 0
        and configuration.value("review.minimum_support") > 0
    )


class SettingsReviewPlanSource:
    def __init__(self, services: ServiceRegistry = service_registry):
        self.services = services

    def plan_for(self, request: ReviewInput, reviewer, provider):
        configuration = request.changes.configuration
        if configuration.value("review.plan") != "premium":
            return None
        choices = configuration.value("model.purposes") or {}
        try:
            self.services.resolve(ModelRouter)
            routed = True
        except LookupError:
            routed = False
        discovery = (
            providers_for(configuration, "review", self.services)
            if routed or "review" in choices
            else (provider,)
        )
        repetitions = configuration.value("review.evaluation_passes")
        evaluation = (
            (
                providers_for(configuration, "review-evaluation", self.services)
                if routed or "review-evaluation" in choices
                else (provider,)
            )
            if repetitions
            else ()
        )
        if not discovery or any(one is None for one in discovery):
            raise ValueError("Premium requires a review model")
        for one in (*discovery, *evaluation):
            if one.missing_credentials():
                raise ValueError("A Premium participant has no configured credentials")
        support = configuration.value("review.minimum_support")
        if support > len(evaluation) * repetitions:
            raise ValueError("Required support exceeds the configured evaluations")
        investigations = ()
        if configuration.value("review.discovery_strategy") == "investigations":
            try:
                source = self.services.resolve(ReviewInvestigationSource)
            except LookupError:
                source = ChangedBehaviorInvestigationSource(
                    getattr(provider, "count_tokens", None)
                )
            limit = configuration.value("review.investigation_limit")
            investigations = tuple(source.investigations_for(request, limit))
            if len(investigations) > limit:
                raise ValueError(
                    "Investigation source exceeded the investigation limit"
                )
            if not investigations:
                raise ValueError("The change has no investigation targets")
        return ParallelReviewPlan(
            reviewers=tuple(
                ReviewParticipant(
                    f"review-{index}",
                    reviewer,
                    one,
                    (
                        len(investigations)
                        if investigations
                        else configuration.value("review.discovery_passes")
                    ),
                    () if investigations else READING_FOCUSES,
                    expert_repetitions=(
                        0
                        if investigations
                        else (
                            min(
                                configuration.value("review.expert_readings"),
                                configuration.value("review.discovery_passes"),
                            )
                            or None
                        )
                    ),
                    investigations=investigations,
                )
                for index, one in enumerate(discovery)
            ),
            evaluators=tuple(
                EvaluationParticipant(
                    f"evaluation-{index}",
                    ModelReviewEvaluator(one, self.services),
                    repetitions,
                )
                for index, one in enumerate(evaluation)
            ),
            concurrency=configuration.value("review.concurrency"),
            minimum_support=support,
            maximum_rejections=configuration.value("review.maximum_rejections"),
        )
