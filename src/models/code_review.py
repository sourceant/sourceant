# src/models/code_review.py
from pydantic import BaseModel, Field
from typing import List, Optional


class CodeSuggestion(BaseModel):
    """Represents a single code suggestion with file and line number."""

    file_name: str = Field(..., description="The name of the file.")
    from_line_number: int = Field(
        ..., description="The starting line number of the suggestion."
    )
    to_line_number: int = Field(
        ..., description="The ending line number of the suggestion."
    )
    suggestion_summary: str = Field(..., description="The suggestion summary.")
    suggestion: str = Field(..., description="The actual suggestion block of code.")


class CodeReview(BaseModel):
    """Represents a comprehensive code review with various feedback categories."""

    code_quality: Optional[str] = Field(
        None, description="Feedback on code quality and style."
    )
    potential_bugs: Optional[str] = Field(
        None, description="Potential bugs or errors identified."
    )
    performance: Optional[str] = Field(
        None, description="Performance considerations and suggestions."
    )
    security: Optional[str] = Field(
        None, description="Security vulnerabilities and recommendations."
    )
    readability: Optional[str] = Field(
        None, description="Feedback on readability and maintainability."
    )
    code_suggestions: List[CodeSuggestion] = Field(
        None, description="A list of actionable code suggestions."
    )
    summary: Optional[str] = Field(None, description="A summary of the code review.")
    refactoring_suggestions: Optional[str] = Field(
        None, description="Refactoring suggestions"
    )
    documentation_suggestions: Optional[str] = Field(
        None, description="Documentation suggestions"
    )
