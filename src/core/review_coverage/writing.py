"""The coverage record as a sentence somebody reads under a review."""

from __future__ import annotations

from .models import (
    Coverage,
    NEIGHBOURING_CODE,
    REACH,
    SIBLING_SOURCE,
)


def read_and_unread(coverage: Coverage) -> str:
    """What this review read, and what it could not.

    Said plainly and last, because a reader who sees no findings should be
    able to tell whether that means the change is fine or that most of the
    system was out of reach.
    """
    read = ["the diff"]
    if coverage.answered(NEIGHBOURING_CODE):
        read.append("the code around the change")
    reached = coverage.reached
    if reached:
        answered = sum(1 for name in reached if coverage.answered(SIBLING_SOURCE, name))
        read.append(
            f"{answered} of {len(reached)} "
            + ("systems" if len(reached) != 1 else "system")
            + " this change reaches"
        )
    elif coverage.answered(REACH):
        read.append("the system graph, which says this change reaches nothing else")

    lines = [f"Read: {', '.join(read)}."]
    unread = coverage.unread
    if unread:
        lines.append(
            "Not read: "
            + "; ".join(f"{name} ({reason})" for name, reason in unread)
            + ". Findings about those boundaries may be missing."
        )
    if not coverage.answered(REACH):
        lines.append(
            "The system graph could not say what this change reaches, so "
            "nothing outside this repository was read."
        )
    return "\n".join(lines)
