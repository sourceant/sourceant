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


class CodeSuggestion(BaseModel):
    """Represents a single code suggestion with file and line number."""

    file_name: str = Field(..., description="The name of the file.")
    position: Optional[int] = Field(..., description="The position of the suggestion.")
    line: Optional[int] = Field(..., description="The line number of the suggestion.")
    start_line: Optional[int] = Field(
        ..., description="The start line number of the suggestion."
    )
    side: Optional[Side] = Field(
        ...,
        description="The side of the suggestion. New changes appear the RIGHT side.",
    )
    comment: str = Field(..., description="The suggestion summary or comment.")
    category: Optional[str] = Field(
        ...,
        description="The category of the suggestion, ex: 'style', 'performance', etc.",
    )
    suggested_code: Optional[str] = Field(
        ...,
        description="The actual suggestion block of code. HIGHLY RECOMMENDED to include.",
    )


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
    summary: Optional[str] = Field(None, description="A summary of the code review.")
    verdict: Verdict = Field(..., description="The overall verdict of the code review.")
    scores: Optional[CodeReviewScores] = Field(
        None, description="A set of scores for different aspects of the code review."
    )
