# src/models/code_review.py
from pydantic import BaseModel, Field
from typing import List, Optional
import enum


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
    CLARITY = "CLARITY"
    DOCUMENTATION = "DOCUMENTATION"
    IMPROVEMENT = "IMPROVEMENT"


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
    comment: str = Field(..., description="The suggestion summary or comment.")
    category: Optional[SuggestionCategory] = Field(
        ...,
        description="The category of the suggestion, ex: 'style', 'performance', etc.",
    )
    suggested_code: Optional[str] = Field(
        ...,
        description="The actual suggestion block of code. HIGHLY RECOMMENDED to include.",
    )
    existing_code: Optional[str] = Field(
        None,
        description="The original code to be replaced. If provided, this is used to anchor the suggestion instead of line numbers.",
    )

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


class CodeReviewSummary(BaseModel):
    """Represents a structured summary of the code review."""

    overview: str = Field(
        ...,
        description="A high-level overview of the code changes and the review.",
    )
    key_improvements: List[str] = Field(
        ...,
        description="A list of key improvements, which may contain references to paths.",
    )
    minor_suggestions: List[str] = Field(
        ...,
        description="A list of minor suggestions and potential enhancements (nice to haves).",
    )
    critical_issues: List[str] = Field(
        ...,
        description="A list of critical issues that should be changed. Leave empty if none.",
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
