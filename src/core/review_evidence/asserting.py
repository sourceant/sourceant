"""Absence claimed in words, turned into a claim that can be checked.

A review looks at a hunk and says a name is not defined. Sometimes it is,
three hundred lines above what the review was shown, and the review has
reported a failure that cannot happen. That is the one thing a bounded view of
a file reliably gets wrong, and the only defence against it was the review
declaring the claim itself so it could be checked. A review confident enough
to assert it in prose is exactly the one that does not declare it.
"""

from __future__ import annotations

import re

from .models import ReviewClaim, StructuralPredicate

#: A name as a review writes one: in backticks, so prose is not mistaken for
#: code.
NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

#: Saying something is not there. Each of these is an assertion of absence,
#: which is what a reader of part of a file cannot support.
ABSENT = re.compile(
    r"\b(?:"
    r"(?:is|are|was|were)\s+(?:not|never)\s+(?:defined|imported|declared)"
    r"|(?:is|are)\s+undefined"
    r"|undefined\s+(?:name|variable|reference)"
    r"|needs?\s+to\s+be\s+(?:defined|imported|declared)"
    r"|must\s+be\s+(?:defined|imported|declared)"
    r"|has\s+not\s+been\s+(?:defined|imported|declared)"
    r"|not\s+defined\s+(?:in|within|anywhere)"
    r")",
    re.IGNORECASE,
)

#: Where one assertion stops and the next begins.
SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n")

#: Beyond a handful, this is reading prose rather than a claim.
MAX_CLAIMS = 4


def claimed_absent(comment: str) -> tuple[ReviewClaim, ...]:
    """What this comment asserts is missing, as claims a file can answer.

    One sentence at a time, and attributed to the nearest name before the
    assertion, because that is the one the sentence is about: in "`x` is used
    here but `y` is not defined" the missing name is `y`.
    """
    claims: dict[tuple[str, str], ReviewClaim] = {}
    for sentence in SENTENCE.split(comment or ""):
        names = [(match.start(), match.group(1)) for match in NAME.finditer(sentence)]
        if not names:
            continue
        for assertion in ABSENT.finditer(sentence):
            before = [name for at, name in names if at < assertion.start()]
            subject = before[-1] if before else names[0][1]
            predicate = (
                StructuralPredicate.IMPORTED
                if "import" in assertion.group(0).lower()
                else StructuralPredicate.DEFINED
            )
            claims.setdefault(
                (subject, predicate.value),
                ReviewClaim(subject=subject, predicate=predicate, expected=False),
            )
            if len(claims) >= MAX_CLAIMS:
                return tuple(claims.values())
    return tuple(claims.values())
