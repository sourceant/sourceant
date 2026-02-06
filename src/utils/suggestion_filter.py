# src/utils/suggestion_filter.py

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple
from src.models.code_review import CodeSuggestion
from src.utils.logger import logger


class SuggestionFilter:
    """
    Filters out non-actionable, positive, or redundant code suggestions.
    """

    POSITIVE_PATTERNS = [
        r"\b(good|great|excellent|nice|well done|perfect|correctly|properly)\b",
        r"\b(looks good|lgtm|ship it|no issues|no problems)\b",
        r"\b(appropriate|suitable|adequate|sufficient)\b",
        r"\bthis is (a )?(good|great|correct|proper)\b",
        r"\b(correctly (implemented|handled|used))\b",
        r"\b(proper(ly)? (implemented|handled|used))\b",
        r"\b(already|currently) (correct|good|proper|fine)\b",
        r"\bno (changes?|improvements?|modifications?) (needed|required|necessary)\b",
        r"\bkeep (it |this )?(as is|unchanged)\b",
    ]

    NEGATIVE_INDICATORS = [
        r"\b(bug|error|issue|problem|flaw|vulnerability)\b",
        r"\b(should|could|might|consider|recommend|suggest)\b",
        r"\b(missing|lacks?|needs?|requires?)\b",
        r"\b(incorrect|wrong|invalid|broken|fails?)\b",
        r"\b(improve|fix|refactor|optimize|simplify)\b",
        r"\b(avoid|don'?t|shouldn'?t|never)\b",
        r"\b(instead|rather|better|prefer)\b",
        r"\b(risk|dangerous|unsafe|insecure)\b",
        r"\b(redundant|unnecessary|unused|dead)\b",
        r"\b(inconsistent|confusing|unclear|ambiguous)\b",
    ]

    def __init__(self):
        self._positive_regex = re.compile(
            "|".join(self.POSITIVE_PATTERNS), re.IGNORECASE
        )
        self._negative_regex = re.compile(
            "|".join(self.NEGATIVE_INDICATORS), re.IGNORECASE
        )

    def filter_suggestions(
        self, suggestions: List[CodeSuggestion]
    ) -> Tuple[List[CodeSuggestion], List[CodeSuggestion]]:
        """
        Filter suggestions, returning (kept, removed) tuples.

        Args:
            suggestions: List of CodeSuggestion objects

        Returns:
            Tuple of (kept_suggestions, removed_suggestions)
        """
        kept = []
        removed = []

        for suggestion in suggestions:
            is_valid, reason = self._is_actionable(suggestion)
            if is_valid:
                kept.append(suggestion)
            else:
                logger.info(
                    f"Filtered out suggestion for "
                    f"{suggestion.file_name}:{suggestion.start_line} - {reason}"
                )
                removed.append(suggestion)

        logger.info(
            f"Suggestion filter: kept {len(kept)}, "
            f"removed {len(removed)} of {len(suggestions)} total"
        )
        return kept, removed

    def _is_actionable(self, suggestion: CodeSuggestion) -> Tuple[bool, str]:
        """
        Determine if a suggestion is actionable.

        Returns:
            Tuple of (is_actionable, reason_if_not)
        """
        if not suggestion.comment:
            return False, "empty comment"

        if not suggestion.suggested_code:
            return False, "no suggested code"

        if self._is_code_identical(suggestion.existing_code, suggestion.suggested_code):
            return False, "suggested code identical to existing code"

        if self._is_positive_only(suggestion.comment):
            return False, "positive-only comment without actionable feedback"

        if not self._has_negative_indicators(suggestion.comment):
            return False, "informational comment without actionable feedback"

        return True, ""

    def _is_code_identical(
        self, existing: Optional[str], suggested: Optional[str]
    ) -> bool:
        """Check if existing and suggested code are essentially identical."""
        if not existing or not suggested:
            return False

        normalized_existing = self._normalize_code(existing)
        normalized_suggested = self._normalize_code(suggested)

        if normalized_existing == normalized_suggested:
            return True

        similarity = SequenceMatcher(
            None, normalized_existing, normalized_suggested
        ).ratio()
        return similarity > 0.95

    def _normalize_code(self, code: str) -> str:
        """Normalize code for comparison by removing insignificant differences."""
        lines = code.strip().splitlines()
        normalized_lines = []
        for line in lines:
            normalized = " ".join(line.split())
            if normalized:
                normalized_lines.append(normalized)
        return "\n".join(normalized_lines)

    def _has_negative_indicators(self, comment: str) -> bool:
        """Check if a comment contains any negative/actionable indicators."""
        return bool(self._negative_regex.search(comment))

    def _is_positive_only(self, comment: str) -> bool:
        """
        Detect if a comment is purely positive without actionable feedback.
        Returns True if the comment is just praise without criticism.
        """
        has_positive = bool(self._positive_regex.search(comment))
        has_negative = bool(self._negative_regex.search(comment))

        if has_positive and not has_negative:
            return True

        return False
