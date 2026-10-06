from __future__ import annotations

from src.core.model import ModelRouter, providers_for
from src.core.services import ServiceRegistry, service_registry
from .evaluation import ModelReviewEvaluator
from .execution import (
    EvaluationParticipant,
    ParallelReviewPlan,
    ReviewInput,
    ReviewParticipant,
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
        return ParallelReviewPlan(
            reviewers=tuple(
                ReviewParticipant(
                    f"review-{index}",
                    reviewer,
                    one,
                    configuration.value("review.discovery_passes"),
                )
                for index, one in enumerate(discovery)
            ),
            evaluators=tuple(
                EvaluationParticipant(
                    f"evaluation-{index}", ModelReviewEvaluator(one), repetitions
                )
                for index, one in enumerate(evaluation)
            ),
            concurrency=configuration.value("review.concurrency"),
            minimum_support=support,
            maximum_rejections=configuration.value("review.maximum_rejections"),
        )
