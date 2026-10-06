from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from src.core.analysis import Analysis
from src.core.change_context import ChangeSet
from src.models.code_review import CodeReview, CodeSuggestion

if TYPE_CHECKING:
    from src.llms.llm_interface import LLMInterface
    from .interfaces import Reviewer, ReviewEvaluator


class EvaluationStatus(str, Enum):
    SUPPORTED = "supported"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class FindingEvaluation:
    candidate: int
    status: EvaluationStatus
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewEvaluation:
    judgments: tuple[FindingEvaluation, ...] = ()
    analysis: Analysis | None = None


@dataclass(frozen=True)
class ReviewInput:
    changes: ChangeSet
    read_content: Callable[[str], str | None] | None = None
    root: Path | None = None
    analysis: Analysis | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewParticipant:
    name: str
    reviewer: Reviewer
    provider: LLMInterface
    repetitions: int = 1


@dataclass(frozen=True)
class EvaluationParticipant:
    name: str
    evaluator: ReviewEvaluator
    repetitions: int = 1


@dataclass(frozen=True)
class ParallelReviewPlan:
    reviewers: tuple[ReviewParticipant, ...]
    evaluators: tuple[EvaluationParticipant, ...] = ()
    concurrency: int = 6
    minimum_support: int = 0
    maximum_rejections: int = 0

    def __post_init__(self) -> None:
        if not self.reviewers:
            raise ValueError("A review plan needs at least one reviewer")
        if self.concurrency < 1:
            raise ValueError("Concurrency must be positive")
        if self.minimum_support < 0 or self.maximum_rejections < 0:
            raise ValueError("Evaluation thresholds cannot be negative")
        for participants in (self.reviewers, self.evaluators):
            names = [one.name for one in participants]
            if any(not name for name in names) or len(names) != len(set(names)):
                raise ValueError("Participant names must be nonempty and unique")
            if any(one.repetitions < 1 for one in participants):
                raise ValueError("Repetitions must be positive")


@dataclass(frozen=True)
class ParticipantOutcome:
    participant: str
    repetition: int
    result: CodeReview | ReviewEvaluation | None = None
    error: str = ""


@dataclass(frozen=True)
class ReviewExecution:
    review: CodeReview | None
    candidates: tuple[CodeSuggestion, ...]
    producers: tuple[tuple[str, ...], ...]
    reviews: tuple[ParticipantOutcome, ...]
    evaluations: tuple[ParticipantOutcome, ...]
    analysis: Analysis | None = None
