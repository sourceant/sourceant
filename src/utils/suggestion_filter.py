# src/utils/suggestion_filter.py

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from src.config.settings import (
    REVIEW_MISSING_EXISTING_CODE_POLICY,
    POSITIVE_SENTIMENT_THRESHOLD,
)
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

    ACTIONABLE_VERBS = [
        r"\b(add|guard|validate|handle|ensure|remove)\b",
        r"\b(refactor|rename|extract|simplify|split|inline)\b",
        r"\b(catch|raise|document)\b",
        r"\b(check|return|log|move)\s+\w+",
        r"\b(replace|reorder|restructure)\b",
        r"\buse\s+(a|an|the|\w+ing)\b",
        r"\bavoid\s+\w+",
    ]

    def __init__(self):
        self._positive_regex = re.compile(
            "|".join(self.POSITIVE_PATTERNS), re.IGNORECASE
        )
        self._negative_regex = re.compile(
            "|".join(self.NEGATIVE_INDICATORS), re.IGNORECASE
        )
        self._actionable_regex = re.compile(
            "|".join(self.ACTIONABLE_VERBS), re.IGNORECASE
        )
        self._sentiment_analyzer = SentimentIntensityAnalyzer()

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

        if not suggestion.existing_code:
            policy = REVIEW_MISSING_EXISTING_CODE_POLICY
            if policy not in {"drop", "warn", "keep"}:
                logger.warning(
                    f"Unknown REVIEW_MISSING_EXISTING_CODE_POLICY '{policy}', defaulting to drop."
                )
                policy = "drop"
            if policy == "drop":
                return False, "missing existing_code"
            if policy == "warn":
                logger.warning(
                    "Suggestion missing existing_code; keeping due to policy."
                )

        if self._is_code_identical(suggestion.existing_code, suggestion.suggested_code):
            return False, "suggested code identical to existing code"

        if self._is_positive_only(suggestion.comment):
            return False, "positive-only comment without actionable feedback"

        scores = self._sentiment_analyzer.polarity_scores(suggestion.comment)
        has_negative_keywords = self._has_negative_indicators(suggestion.comment)
        has_actionable_verbs = self._has_actionable_verbs(suggestion.comment)
        is_negative_sentiment = scores["compound"] <= -0.05

        if not has_negative_keywords and not is_negative_sentiment:
            if not has_actionable_verbs:
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
            line = self._strip_diff_prefix(line)
            normalized = " ".join(line.split())
            if normalized:
                normalized_lines.append(normalized)
        return "\n".join(normalized_lines)

    def _strip_diff_prefix(self, line: str) -> str:
        """Strip leading diff markers from a single line."""
        if not line:
            return line
        if line[0] in {"+", "-"}:
            return line[1:]
        return line

    def _has_negative_indicators(self, comment: str) -> bool:
        """Check if a comment contains any negative/actionable indicators."""
        return bool(self._negative_regex.search(comment))

    def _has_actionable_verbs(self, comment: str) -> bool:
        """Check if a comment contains actionable verbs."""
        return bool(self._actionable_regex.search(comment))

    def _is_positive_only(self, comment: str) -> bool:
        """
        Detect if a comment is purely positive without actionable feedback.
        Returns True if the comment is just praise without criticism.
        """
        has_negative = bool(self._negative_regex.search(comment))
        if has_negative:
            return False

        if self._has_actionable_verbs(comment):
            return False

        scores = self._sentiment_analyzer.polarity_scores(comment)
        if scores["compound"] >= POSITIVE_SENTIMENT_THRESHOLD:
            return True

        return bool(self._positive_regex.search(comment))
