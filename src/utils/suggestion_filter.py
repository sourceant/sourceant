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
        r"\b(good|great|excellent|nice|clean|well done|perfect|correctly|properly)\b",
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
        r"\b(bugs?|errors?|issues?|problems?|flaws?|vulnerabilit(?:y|ies)|"
        r"crash(?:es)?|exceptions?|regressions?)\b",
        r"\bnull dereference\b",
        r"\b(should|could|might|consider|recommend|suggest)\b",
        r"\b(missing|lacks?|needs?|requires?)\b",
        r"\b(incorrect|wrong|invalid|broken|fails?)\b",
        # How a reviewer reports a fault at runtime, rather than naming it.
        r"\b(throws?|throwing|raises?|raising|panics?|hangs?|leaks?)\b",
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

    COMPLETED_ACTIONS = [
        r"\b(?:remov(?:e|es|ed|ing)|replac(?:e|es|ed|ing)|"
        r"simplif(?:y|ies|ied|ying)|fix(?:es|ed|ing)?|"
        r"address(?:es|ed|ing)?|avoid(?:s|ed|ing)?)\b",
    ]

    ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "resp.")

    UNRESOLVED_TRANSITIONS = [
        r"\b(?:but|however|although|though|yet|nevertheless)\b",
    ]

    UNRESOLVED_HARM = [
        r"\b(?:introduces?|causes?|creates?|leads? to|still|remains?|fails?)\b"
        r".{0,80}\b(?:bugs?|errors?|failures?|crash(?:es)?|exceptions?|"
        r"vulnerabilit(?:y|ies)|"
        r"regressions?|null dereferences?)\b",
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
        self._completed_action_regex = re.compile(
            "|".join(self.COMPLETED_ACTIONS), re.IGNORECASE
        )
        self._unresolved_transition_regex = re.compile(
            "|".join(self.UNRESOLVED_TRANSITIONS), re.IGNORECASE
        )
        self._unresolved_harm_regex = re.compile(
            "|".join(self.UNRESOLVED_HARM), re.IGNORECASE
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

        if self._is_completed_change_praise(suggestion.comment):
            return False, "comment praises a completed change"

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

    def _is_completed_change_praise(self, comment: str) -> bool:
        sentences = self._sentences(comment)
        return (
            bool(sentences)
            and any(
                self._completed_action_regex.search(sentence) for sentence in sentences
            )
            and all(self._is_positive_sentence(sentence) for sentence in sentences)
        )

    def _is_positive_sentence(self, sentence: str) -> bool:
        if self._unresolved_transition_regex.search(
            sentence
        ) or self._unresolved_harm_regex.search(sentence):
            return False

        has_completed_action = bool(self._completed_action_regex.search(sentence))
        if self._has_actionable_verbs(sentence) and not has_completed_action:
            return False

        scores = self._sentiment_analyzer.polarity_scores(sentence)
        has_positive = scores["compound"] >= POSITIVE_SENTIMENT_THRESHOLD or bool(
            self._positive_regex.search(sentence)
        )
        if not has_positive:
            return False

        has_negative = bool(self._negative_regex.search(sentence))
        return not has_negative or has_completed_action

    def _sentences(self, comment: str) -> List[str]:
        sentences: List[str] = []
        for part in self._split_sentences(comment):
            part = part.strip()
            if not part:
                continue
            # An abbreviation ends in a full stop without ending a sentence, so
            # what follows belongs to the sentence before it.
            if sentences and sentences[-1].lower().endswith(self.ABBREVIATIONS):
                sentences[-1] = f"{sentences[-1]} {part}"
            else:
                sentences.append(part)
        return sentences

    def _split_sentences(self, comment: str) -> List[str]:
        """Split on sentence ends, but not on punctuation inside inline code.

        A comment quotes the code it is about, and that code carries the same
        characters a sentence ends with.
        """
        parts: List[str] = []
        cursor = 0
        for match in re.finditer(r"(?<=[.!?])\s+|\n+", comment):
            # An odd number of backticks before this point means it falls inside
            # a quoted span, where a full stop ends nothing.
            if comment.count("`", 0, match.start()) % 2:
                continue
            parts.append(comment[cursor : match.start()])
            cursor = match.end()
        parts.append(comment[cursor:])
        return parts

    def _is_positive_only(self, comment: str) -> bool:
        """
        Detect if a comment is purely positive without actionable feedback.
        Returns True if the comment is just praise without criticism.
        """
        sentences = self._sentences(comment)
        return bool(sentences) and all(
            self._is_positive_sentence(sentence) for sentence in sentences
        )
