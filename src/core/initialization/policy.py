from __future__ import annotations

import re

from .models import CandidateAssessment, InitializationCandidate


class DefaultInitializationCandidatePolicy:
    _inventory_summary = re.compile(
        r"^(?:(?:the|this)\s+)?"
        r"(?:[\w.-]+\s+){0,5}?"
        r"(?:uses?|has|contains?|includes?|defines?|runs?|depends\s+on|"
        r"lives\s+in|"
        r"is\s+(?:built|written|implemented)\s+(?:with|in)|"
        r"is\s+powered\s+by|is\s+built\s+on)\b",
        re.IGNORECASE,
    )

    def assess(self, candidate: InitializationCandidate) -> CandidateAssessment:
        reasons = []
        if self._inventory_summary.search(candidate.summary.strip()):
            reasons.append("summary describes repository inventory")
        return CandidateAssessment(not reasons, tuple(reasons))
