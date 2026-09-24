# src/models/code_review.py
from pydantic import BaseModel, Field
from typing import List, Optional
import enum

from src.core.review_evidence import ReviewClaim


class Verdict(enum.Enum):
    """Represents the overall verdict of the code review."""

    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


class Side(enum.Enum):
    """Represents the side of the code suggestion."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"


class SuggestionCategory(enum.Enum):
    """Represents the category of the suggestion."""

    REFACTOR = "REFACTOR"
    STYLE = "STYLE"
    PERFORMANCE = "PERFORMANCE"
    BUG = "BUG"
    SECURITY = "SECURITY"
    CLARITY = "CLARITY"
    DOCUMENTATION = "DOCUMENTATION"
    IMPROVEMENT = "IMPROVEMENT"


class Reach(enum.Enum):
    """Who can get to the line a finding is about."""

    ANYONE = "anyone"
    AUTHENTICATED = "authenticated"
    OPERATOR = "operator"
    UNREACHABLE = "unreachable"


class Impact(enum.Enum):
    """What happens when they do, worst first."""

    DATA_LOSS = "data_loss"
    CORRUPTION = "corruption"
    DISCLOSURE = "disclosure"
    WRONG_ANSWER = "wrong_answer"
    HANG = "hang"
    CRASH = "crash"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    NONE = "none"


class Severity(enum.Enum):
    """How much a finding matters."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"
    NIT = "nit"


_REACH_ORDER = (Reach.ANYONE, Reach.AUTHENTICATED, Reach.OPERATOR, Reach.UNREACHABLE)
_B, _A, _N = Severity.BLOCKING, Severity.ADVISORY, Severity.NIT

#: Severity is policy, so it is written once here rather than decided per
#: finding by whichever model answered.
_SEVERITY = {
    impact: dict(zip(_REACH_ORDER, row))
    for impact, row in {
        Impact.DATA_LOSS: (_B, _B, _B, _A),
        Impact.CORRUPTION: (_B, _B, _B, _A),
        Impact.DISCLOSURE: (_B, _B, _A, _A),
        Impact.WRONG_ANSWER: (_B, _B, _A, _A),
        Impact.HANG: (_B, _B, _A, _A),
        Impact.CRASH: (_B, _A, _A, _A),
        Impact.DEGRADED: (_A, _A, _A, _N),
        Impact.REJECTED: (_A, _A, _A, _N),
        Impact.NONE: (_N, _N, _N, _N),
    }.items()
}

_RANKED_BY_CATEGORY = {SuggestionCategory.BUG, SuggestionCategory.SECURITY}
_WORTH_SAYING = _RANKED_BY_CATEGORY | {SuggestionCategory.PERFORMANCE}


def severity_of(suggestion) -> Severity:
    """How much one finding matters.

    A suggestion that answered neither question is ranked by its category,
    which is what ranked every finding before the questions existed.
    """
    reach = getattr(suggestion, "reach", None)
    impact = getattr(suggestion, "impact", None)
    if reach is None or impact is None:
        category = getattr(suggestion, "category", None)
        return _B if category in _RANKED_BY_CATEGORY else _A
    return _SEVERITY[impact][reach]


def is_nitpick(suggestion) -> bool:
    """Advice a reader is free to ignore.

    A suggestion with no impact to judge is filtered by the category it was
    filtered by before.
    """
    if getattr(suggestion, "impact", None) is None:
        return getattr(suggestion, "category", None) not in _WORTH_SAYING
    return severity_of(suggestion) is Severity.NIT


class CodeSuggestion(BaseModel):
    """Represents a single code suggestion with file and line number."""

    file_name: str = Field(..., description="The name of the file.")
    position: Optional[int] = Field(
        None, description="The position of the suggestion in the diff."
    )
    start_line: int = Field(
        ..., description="The first line of the code block to be replaced."
    )
    end_line: int = Field(
        ...,
        description="The last line of the code block to be replaced. For single-line comments, this is the same as start_line.",
    )
    side: Optional[Side] = Field(
        ...,
        description="The side of the suggestion. New changes appear the RIGHT side.",
    )
    comment: str = Field(
        ...,
        description="The problem, consequence, and fix in at most three sentences and 60 words.",
    )
    category: Optional[SuggestionCategory] = Field(
        ...,
        description="The category of the suggestion, ex: 'style', 'performance', etc.",
    )
    reach: Optional[Reach] = Field(
        None,
        description=(
            "Who can get to this line. 'anyone' if an unauthenticated caller "
            "can, 'authenticated' if any signed-in user can, 'operator' if "
            "only an administrator or a deploy can, 'unreachable' if no "
            "caller can get here at all."
        ),
    )
    impact: Optional[Impact] = Field(
        None,
        description=(
            "What happens when they do. 'data_loss' if correct data is "
            "destroyed with no way back, 'corruption' if wrong values are "
            "written and kept with nothing signalling it, 'disclosure' if "
            "data reaches someone who should not see it, 'wrong_answer' if "
            "the caller gets an incorrect result that is not persisted, "
            "'hang' if it does not finish or consumes unbounded resources, "
            "'crash' if the operation dies unexpectedly, 'degraded' if it "
            "works but costs more time or money than it should, 'rejected' "
            "if the bad path is already refused with a clear error, 'none' "
            "if there is no runtime consequence at all."
        ),
    )
    suggested_code: Optional[str] = Field(
        ...,
        description="The actual suggestion block of code. HIGHLY RECOMMENDED to include.",
    )
    existing_code: Optional[str] = Field(
        None,
        description="The original code to be replaced. If provided, this is used to anchor the suggestion instead of line numbers.",
    )
    claims: List[ReviewClaim] = Field(
        default_factory=list,
        description=(
            "Machine-checkable factual claims this suggestion depends on. State "
            "every assertion that a name is or is not defined or imported. A "
            "claim contradicted by the file is discarded with its suggestion, "
            "so an unstated claim is an unchecked one. Empty only when the "
            "suggestion asserts nothing about a definition or an import."
        ),
    )

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        """Optional to write here, required to answer with.

        Structured output follows the schema rather than the prose asking for
        it: left optional, the field is omitted, every assertion goes
        unchecked and every finding falls back to being ranked by category.
        Nothing in this codebase has to state them to build one.
        """
        schema = handler(core_schema)
        required = list(schema.get("required", []))
        for field in ("claims", "reach", "impact"):
            if field not in required:
                required.append(field)
        schema["required"] = required
        return schema

    def is_multiline(self) -> bool:
        """Checks if the suggestion spans multiple lines."""
        return self.start_line != self.end_line


class CodeReviewScores(BaseModel):
    """Represents the scores for different aspects of the code review."""

    correctness: int = Field(
        ...,
        description="Score for correctness, logic, and common mistakes (1-10).",
        ge=1,
        le=10,
    )
    clarity: int = Field(
        ..., description="Score for clarity and readability (1-10).", ge=1, le=10
    )
    maintainability: int = Field(
        ...,
        description="Score for maintainability and scalability (1-10).",
        ge=1,
        le=10,
    )
    security: int = Field(
        ..., description="Score for security aspects (1-10).", ge=1, le=10
    )
    performance: int = Field(
        ..., description="Score for performance (1-10).", ge=1, le=10
    )


class CodeReviewOverview(BaseModel):
    """Represents a structured summary of the code review."""

    overview: str = Field(
        ...,
        description="Main behavioral changes in at most three sentences and 75 words.",
    )
    key_improvements: List[str] = Field(
        default_factory=list,
        description=(
            "At most three key improvements, each at most 20 words. "
            "Leave empty if none."
        ),
    )
    regressions: List[str] = Field(
        default_factory=list,
        description="At most three regressions, each at most 20 words. Leave empty if none.",
    )


class CodeReviewSummary(CodeReviewOverview):
    minor_suggestions: List[str] = Field(
        ...,
        description="A list of minor suggestions and potential enhancements (nice to haves).",
    )
    critical_issues: List[str] = Field(
        ...,
        description="A list of critical issues that should be changed. Leave empty if none.",
    )
    systems: Optional[str] = Field(
        None,
        description=(
            "Leave this empty. It is filled in afterwards with what was found "
            "in the systems this change reaches."
        ),
    )
    coverage: Optional[str] = Field(
        None,
        description=(
            "Leave this empty. It is filled in afterwards with what the "
            "review was able to read, which is not something a reading of "
            "the diff can know."
        ),
    )


class CodeReview(BaseModel):
    """Represents a comprehensive code review with various feedback categories."""

    code_quality: Optional[str] = Field(
        None, description="Feedback on code quality and style."
    )
    code_suggestions: List[CodeSuggestion] = Field(
        None, description="A list of actionable code suggestions."
    )
    documentation_suggestions: Optional[str] = Field(
        None, description="Documentation suggestions"
    )
    potential_bugs: Optional[str] = Field(
        None, description="Potential bugs or errors identified."
    )
    performance: Optional[str] = Field(
        None, description="Performance considerations and suggestions."
    )
    readability: Optional[str] = Field(
        None, description="Feedback on readability and maintainability."
    )
    refactoring_suggestions: Optional[str] = Field(
        None, description="Refactoring suggestions"
    )
    security: Optional[str] = Field(
        None, description="Security vulnerabilities and recommendations."
    )
    summary: Optional[CodeReviewSummary] = Field(
        None, description="A structured summary of the code review."
    )
    verdict: Verdict = Field(..., description="The overall verdict of the code review.")
    scores: Optional[CodeReviewScores] = Field(
        None, description="A set of scores for different aspects of the code review."
    )


class CodeReviewFindings(CodeReview):
    summary: None = None


def summary_from(
    suggestions: List, written: CodeReviewOverview | None = None
) -> CodeReviewSummary:
    """The findings that survived, under whatever was written about the change.

    What was written about the change is not a claim about a defect, so a
    finding that failed its evidence check is no reason to lose it. Dropped
    with the findings, a review of a change nobody could fault reads exactly
    like a review that found nothing to say.
    """
    critical = [
        suggestion.comment
        for suggestion in suggestions
        if severity_of(suggestion) is Severity.BLOCKING
    ]
    minor = [
        suggestion.comment
        for suggestion in suggestions
        if severity_of(suggestion) is not Severity.BLOCKING
    ]
    counted = (
        f"Review found {len(suggestions)} actionable issue(s)."
        if suggestions
        else "No actionable issues were found."
    )
    return CodeReviewSummary(
        overview=(written.overview if written and written.overview else counted),
        key_improvements=list(written.key_improvements) if written else [],
        regressions=list(written.regressions) if written else [],
        minor_suggestions=minor,
        critical_issues=critical,
    )
