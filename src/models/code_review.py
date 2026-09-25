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


class Trigger(enum.Enum):
    """What causes the finding to happen."""

    ANYONE = "anyone"
    AUTHENTICATED = "authenticated"
    OPERATOR = "operator"
    SERVICE = "service"
    EVENT = "event"
    AUTOMATION = "automation"
    DEPLOY = "deploy"
    ENVIRONMENT = "environment"


class Blast(enum.Enum):
    """Who is worse off once it does."""

    EVERYONE = "everyone"
    MANY = "many"
    ONE = "one"
    NOBODY = "nobody"


class Impact(enum.Enum):
    """What goes wrong, worst first."""

    DATA_LOSS = "data_loss"
    CORRUPTION = "corruption"
    DISCLOSURE = "disclosure"
    ESCALATION = "escalation"
    WRONG_ANSWER = "wrong_answer"
    HANG = "hang"
    CRASH = "crash"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    NONE = "none"


class Certainty(enum.Enum):
    """Whether it happens, or only could."""

    ALWAYS = "always"
    CONDITIONAL = "conditional"
    POSSIBLE = "possible"


class Severity(enum.Enum):
    """How much a finding matters."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"
    NIT = "nit"


#: Short because the table below is read as a table. Worst first, so a step
#: along it is a step towards advice a reader can ignore.
_B, _A, _N = Severity.BLOCKING, Severity.ADVISORY, Severity.NIT
_LADDER = (_B, _A, _N)

#: What the impact alone would be, before anything about who or how often.
_BASE = {
    Impact.DATA_LOSS: _B,
    Impact.CORRUPTION: _B,
    Impact.DISCLOSURE: _B,
    Impact.ESCALATION: _B,
    Impact.WRONG_ANSWER: _B,
    Impact.HANG: _B,
    Impact.CRASH: _B,
    Impact.DEGRADED: _A,
    Impact.REJECTED: _A,
    Impact.NONE: _N,
}

#: Nothing wrong survives these, so how many people arrive does not make them
#: worse: a refusal refuses the same way for everybody, and a naming choice in
#: a much-used function is still a naming choice.
_NOTHING_PERSISTS = {Impact.REJECTED, Impact.NONE}

#: The impacts somebody with administrative authority could already cause
#: without the defect, so reaching them that way is not the escalation it is
#: for anybody else. A deploy counts; unattended work does not, because it
#: has no authority to be already holding, and an inbound event does not,
#: because whoever sent it may not be trusted at all.
_ALREADY_AUTHORISED = {Trigger.OPERATOR, Trigger.DEPLOY}
_BEYOND_AUTHORITY = {Impact.DISCLOSURE, Impact.ESCALATION}

_RANKED_BY_CATEGORY = {SuggestionCategory.BUG, SuggestionCategory.SECURITY}
_WORTH_SAYING = _RANKED_BY_CATEGORY | {SuggestionCategory.PERFORMANCE}

_QUESTIONS = ("trigger", "blast", "impact", "certainty")


def _softer(severity: Severity, steps: int) -> Severity:
    """Move along the ladder, stopping at either end.

    One step softens. A negative step hardens, which reads backwards and is
    why every caller below says which it means in the condition above it.
    """
    at = _LADDER.index(severity) + steps
    return _LADDER[max(0, min(len(_LADDER) - 1, at))]


def _answered(suggestion):
    """What a finding was ranked by, or None where it did not answer.

    All four or none. Every caller asks this one question so that a missing
    answer cannot mean one thing to the ranking and another to the filter.
    """
    given = [getattr(suggestion, name, None) for name in _QUESTIONS]
    return None if any(answer is None for answer in given) else given


def severity_of(suggestion) -> Severity:
    """How much one finding matters.

    Severity is policy, so it is decided here from what a finding answered
    rather than by whichever model wrote it. A suggestion that did not answer
    is ranked by its category, which is what ranked every finding before the
    questions existed.
    """
    answered = _answered(suggestion)
    if answered is None:
        category = getattr(suggestion, "category", None)
        return _B if category in _RANKED_BY_CATEGORY else _A

    trigger, blast, impact, certainty = answered
    if certainty is Certainty.POSSIBLE:
        return _N

    severity = _BASE[impact]
    if blast is Blast.EVERYONE and impact not in _NOTHING_PERSISTS:
        severity = _softer(severity, -1)  # harder
    elif blast in (Blast.ONE, Blast.NOBODY):
        severity = _softer(severity, 1)
    if trigger in _ALREADY_AUTHORISED and impact in _BEYOND_AUTHORITY:
        severity = _softer(severity, 1)
    if certainty is Certainty.CONDITIONAL:
        severity = _softer(severity, 1)
    return severity


def is_nitpick(suggestion) -> bool:
    """Advice a reader is free to ignore.

    A suggestion that did not answer is filtered by the category it was
    filtered by before.
    """
    if _answered(suggestion) is None:
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
    trigger: Optional[Trigger] = Field(
        None,
        description=(
            "What causes this, which need not be a person. 'anyone' for "
            "an unauthenticated caller, 'authenticated' for any signed-in "
            "user, 'operator' for an administrator acting deliberately, "
            "'service' for another service calling in with its own "
            "credentials, 'event' for an inbound message such as a webhook "
            "or a queue message, 'automation' for work the system starts "
            "itself such as a cron, a sweep, a worker or a retry, 'deploy' "
            "for a release, a migration or a startup path, 'environment' "
            "for a condition rather than a caller, such as a dropped "
            "connection, a full disk, an expired certificate or a clock "
            "change."
        ),
    )
    blast: Optional[Blast] = Field(
        None,
        description=(
            "Who is worse off once it happens. 'everyone' if every user or all "
            "the data is affected, 'many' if a whole class of users is, such "
            "as one tenant or one plan, 'one' if only the caller who caused it "
            "is, 'nobody' if no user is."
        ),
    )
    impact: Optional[Impact] = Field(
        None,
        description=(
            "What goes wrong. 'data_loss' if correct data is destroyed with no "
            "way back, 'corruption' if wrong values are written and kept with "
            "nothing signalling it, which includes an operation that stops "
            "halfway and leaves the rest undone, 'disclosure' if data reaches "
            "someone who should not see it, 'escalation' if someone can act "
            "beyond their authority, 'wrong_answer' if the caller gets an "
            "incorrect result that is not persisted, 'hang' if it does not "
            "finish or consumes unbounded memory, connections or time, "
            "'crash' if the operation dies unexpectedly, 'degraded' if it "
            "works but costs more time or money than it should, 'rejected' if "
            "the bad path is already refused with a clear error, 'none' if "
            "there is no runtime consequence at all."
        ),
    )
    certainty: Optional[Certainty] = Field(
        None,
        description=(
            "Whether it happens. 'always' if the bad path runs every time this "
            "code runs, 'conditional' if it runs on some inputs or in some "
            "states you can name, 'possible' if it depends on an assumption "
            "about code you have not been shown."
        ),
    )
    comment_only: bool = Field(
        False,
        description=(
            "Set true to intentionally report an actionable issue at a specific "
            "line without a replacement patch. Explain the problem, consequence, "
            "and needed action in comment, and set suggested_code to null. "
            "Use false when proposing replacement code. This does not bypass "
            "evidence checks, the missing existing-code policy, or the nitpick policy."
        ),
    )
    suggested_code: Optional[str] = Field(
        ...,
        description="Replacement code, or null when comment_only is true.",
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
        for field in ("claims", "comment_only", *_QUESTIONS):
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
    critical, minor = [], []
    for suggestion in suggestions:
        ranked = critical if severity_of(suggestion) is Severity.BLOCKING else minor
        ranked.append(suggestion.comment)
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
