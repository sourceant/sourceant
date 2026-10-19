from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Mapping, TYPE_CHECKING

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
    duplicate_of: int | None = None
    premises: dict[str, str] | None = None


@dataclass(frozen=True)
class ReviewEvaluation:
    judgments: tuple[FindingEvaluation, ...] = ()
    analysis: Analysis | None = None


@dataclass
class ReviewSourceContext:
    source: dict[str, str] = field(default_factory=dict)
    omitted: list[str] = field(default_factory=list)

    def rendered(self) -> str:
        return "\n\n".join(
            f"Repository contract source: {path}\n"
            + "\n".join(
                f"{line}: {text}" for line, text in enumerate(content.splitlines(), 1)
            )
            for path, content in self.source.items()
        )


@dataclass(frozen=True)
class ReviewInput:
    changes: ChangeSet
    read_content: Callable[[str], str | None] | None = None
    root: Path | None = None
    analysis: Analysis | None = None
    options: dict[str, Any] = field(default_factory=dict)
    source_context: Mapping[str, str] = field(default_factory=dict)
    analysis_task: Future[Analysis] | None = None
    require_complete_discovery: bool = False


@dataclass(frozen=True)
class ReviewTarget:
    path: str
    ranges: tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True)
class ReviewInvestigation:
    name: str
    targets: tuple[ReviewTarget, ...]
    question: str

    def __post_init__(self):
        if not self.name or not self.targets or not self.question.strip():
            raise ValueError("An investigation needs an identity, targets and question")
        paths = [target.path for target in self.targets]
        if len(paths) != len(set(paths)) or any(not path for path in paths):
            raise ValueError("Investigation targets must have unique nonempty paths")


@dataclass(frozen=True)
class ReviewParticipant:
    name: str
    reviewer: Reviewer
    provider: LLMInterface
    repetitions: int = 1
    focuses: tuple[str, ...] = ()
    expert_repetitions: int | None = None
    investigations: tuple[ReviewInvestigation, ...] = ()


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
        if any(
            one.expert_repetitions is not None
            and not 0 <= one.expert_repetitions <= one.repetitions
            for one in self.reviewers
        ):
            raise ValueError("Expert repetitions must fit within discovery repetitions")
        for participant in self.reviewers:
            if (
                participant.investigations
                and len(participant.investigations) != participant.repetitions
            ):
                raise ValueError("Every investigation reading needs its own task")


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
    finding_groups: tuple[tuple[int, ...], ...] = ()
    investigation_gaps: tuple[str, ...] = ()
