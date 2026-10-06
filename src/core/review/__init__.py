"""Reviewing interfaces. Implemented by the code_reviewer plugin."""

from typing import Optional

from src.core.services import ServiceRegistry, service_registry

from .findings import (
    DISMISSED,
    FIXED,
    OPEN,
    FindingQuery,
    FindingResult,
    ReviewFinding,
)
from .findings_memory import InMemoryFindingStore
from .findings_sql import SQLFindingStore
from .fingerprint import prints_for
from .interfaces import (
    FindingReader,
    FindingStore,
    FindingWriter,
    Reviewer,
    ReviewStore,
    WorkingTreeReviewer,
    ReviewEvaluator,
    ReviewOrchestrator,
    ReviewPlan,
    ReviewPlanSource,
)
from .execution import (
    EvaluationParticipant,
    EvaluationStatus,
    FindingEvaluation,
    ParallelReviewPlan,
    ParticipantOutcome,
    ReviewEvaluation,
    ReviewExecution,
    ReviewInput,
    ReviewParticipant,
)
from .evaluation import DeterministicReviewEvaluator, ModelReviewEvaluator
from .orchestration import ParallelReviewOrchestrator
from .planning import SettingsReviewPlanSource
from .models import Sections, Told
from .records import DONE, FAILED, RUNNING, ReviewRecord, named, now
from .sql import SQLReviewStore


def reviewer(services: ServiceRegistry = service_registry) -> Optional[Reviewer]:
    """Whatever registered as a reviewer, or None."""
    try:
        return services.resolve(Reviewer)
    except LookupError:
        return None


def review_orchestrator(
    services: ServiceRegistry = service_registry,
) -> ReviewOrchestrator:
    try:
        return services.resolve(ReviewOrchestrator)
    except LookupError:
        return ParallelReviewOrchestrator(DeterministicReviewEvaluator(services))


def review_plan_source(
    services: ServiceRegistry = service_registry,
) -> ReviewPlanSource:
    try:
        return services.resolve(ReviewPlanSource)
    except LookupError:
        return SettingsReviewPlanSource(services)


def working_tree_reviewer(
    services: ServiceRegistry = service_registry,
) -> Optional[WorkingTreeReviewer]:
    """Whatever registered as a working tree reviewer, or None."""
    try:
        return services.resolve(WorkingTreeReviewer)
    except LookupError:
        return None


def finding_store(services: ServiceRegistry = service_registry):
    """Whatever registered somewhere to keep findings between reviews, or None.

    None is an ordinary answer: a review runs and forgets, which is what it did
    before anything kept them.
    """
    try:
        return services.resolve(FindingStore)
    except LookupError:
        return None


def review_store(services: ServiceRegistry = service_registry):
    """Whatever registered somewhere to keep reviews, or None."""
    try:
        return services.resolve(ReviewStore)
    except LookupError:
        return None


__all__ = [
    "ReviewEvaluator",
    "ReviewOrchestrator",
    "ReviewPlan",
    "review_orchestrator",
    "ReviewPlanSource",
    "SettingsReviewPlanSource",
    "review_plan_source",
    "EvaluationParticipant",
    "EvaluationStatus",
    "FindingEvaluation",
    "ParallelReviewPlan",
    "ParticipantOutcome",
    "ReviewEvaluation",
    "ReviewExecution",
    "ReviewInput",
    "ReviewParticipant",
    "DeterministicReviewEvaluator",
    "ModelReviewEvaluator",
    "ParallelReviewOrchestrator",
    "DISMISSED",
    "DONE",
    "FAILED",
    "RUNNING",
    "FIXED",
    "FindingQuery",
    "FindingReader",
    "FindingResult",
    "FindingStore",
    "FindingWriter",
    "InMemoryFindingStore",
    "SQLFindingStore",
    "OPEN",
    "ReviewFinding",
    "ReviewRecord",
    "ReviewStore",
    "SQLReviewStore",
    "Reviewer",
    "Sections",
    "Told",
    "WorkingTreeReviewer",
    "finding_store",
    "prints_for",
    "named",
    "now",
    "review_store",
    "reviewer",
    "working_tree_reviewer",
]
