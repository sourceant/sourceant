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
    ReviewInvestigation,
    ReviewTarget,
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


def discovery_models(configuration, services, provider):
    choices = configuration.value("model.purposes") or {}
    try:
        services.resolve(ModelRouter)
        routed = True
    except LookupError:
        routed = False
    return (
        providers_for(configuration, "review", services)
        if routed or "review" in choices
        else (provider,)
    )


class SettingsReviewPlanSource:
    def __init__(self, services: ServiceRegistry = service_registry):
        self.services = services

    def plan_for(self, request: ReviewInput, reviewer, provider):
        configuration = request.changes.configuration
        from src.core.review.partitioning import (
            partition_source,
            change_map,
            relationships,
            review_cost,
            reading_budget,
        )
        from src.core.review_performance import current_budget

        planned_models = (
            discovery_models(configuration, self.services, provider)
            if configuration.value("review.plan") == "premium"
            else (provider,)
        )
        if not planned_models:
            raise ValueError("A review model is required")
        budget = min(reading_budget(configuration, model) for model in planned_models)
        costs = tuple(review_cost(model) for model in planned_models)
        partitions = partition_source(self.services).partition(
            getattr(request.changes, "diff", ""),
            cost=lambda text: max(cost(text) for cost in costs),
            input_budget=budget,
            maximum_parts=int(configuration.value("review.maximum_parts") or 64),
            adjacency=(
                relationships(self.services, request.changes.code_scope)
                if hasattr(request.changes, "code_scope")
                else {}
            ),
        )
        investigations = ()
        if len(partitions.chunks) > 1:
            inventory = change_map(partitions)
            investigations = tuple(
                ReviewInvestigation(
                    f"partition-{index}",
                    tuple(
                        ReviewTarget(file.file_path, tuple(file.hunk_ranges))
                        for file in chunk.files
                    ),
                    "Review these exact ranges in the context of the whole change. "
                    "Check interactions with sibling files and report concrete defects.\n"
                    + request.changes.title[:2000]
                    + "\n"
                    + request.changes.description[:6000]
                    + "\nChanged files:\n"
                    + inventory[:16000],
                    "\n".join(file.diff_text for file in chunk.files),
                )
                for index, chunk in enumerate(partitions.chunks)
            )
        if configuration.value("review.plan") != "premium":
            if not investigations:
                return None
            active = current_budget()
            if active is not None:
                active.plan_parts(len(investigations), configuration)
            return ParallelReviewPlan(
                reviewers=(
                    ReviewParticipant(
                        "review-0",
                        reviewer,
                        provider,
                        len(investigations),
                        investigations=investigations,
                    ),
                ),
                concurrency=configuration.value("review.concurrency"),
                integration_provider=provider,
            )
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
        if (
            not investigations
            and configuration.value("review.split_large_changes") is True
        ):
            try:
                source = self.services.resolve(ReviewInvestigationSource)
            except LookupError:
                source = ChangedBehaviorInvestigationSource(
                    getattr(provider, "count_tokens", None)
                )
            limit = configuration.value("review.maximum_parts")
            investigations = tuple(source.investigations_for(request, limit))
            if len(investigations) > limit:
                raise ValueError(
                    "Investigation source exceeded the investigation limit"
                )
            if not investigations:
                raise ValueError("The change has no investigation targets")
        active = current_budget()
        if active is not None and investigations:
            active.plan_parts(
                len(investigations),
                configuration,
                calls_per_part=max(1, len(discovery) + len(evaluation) * repetitions),
            )
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
            integration_provider=provider if len(partitions.chunks) > 1 else None,
        )
