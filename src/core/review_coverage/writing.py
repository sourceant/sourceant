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
    lines = [f"Read: {', '.join(read)}."]
    if not reached and coverage.answered(REACH):
        # Its own sentence. As the last item of a list it needs a clause of
        # its own, and the comma that takes reads as another item.
        lines.append("The system graph says this change reaches nothing else.")
    unread = coverage.unread
    if unread:
        lines.append(
            "Not read: "
            + "; ".join(f"{name} ({reason})" for name, reason in unread)
            + ". Findings about those boundaries may be missing."
        )
    if not coverage.answered(REACH):
        why = next(
            (
                attempt.reason
                for attempt in coverage.attempts
                if attempt.question == REACH and attempt.reason
            ),
            "the system graph could not be read",
        )
        lines.append(f"Nothing outside this repository was read: {why}.")
    return "\n".join(lines)


def systems_read(coverage, suggestions=()) -> str:
    """What this change does to the systems around it, where it does anything.

    Reaching a system is not news, and neither is a list of files that happen
    to carry a word the search asked for. A finding about a system earns a
    line here and the code it was found in goes under it as evidence. Nothing
    to say about anywhere means no section at all.
    """
    lines = []
    for name in coverage.reached_with_code:
        about = [
            one
            for one in suggestions
            if one and one.comment and name.lower() in one.comment.lower()
        ]
        if not about:
            continue
        lines.append(f"**{name}**")
        for one in about:
            lines.append(
                f"  - {one.comment.strip().splitlines()[0]} (`{one.file_name}`)"
            )
        found = coverage.found_in(name)
        if found:
            lines.append(
                "  - Found in: "
                + ", ".join(f"`{path}`" for path in found[:3])
                + (f" and {len(found) - 3} more" if len(found) > 3 else "")
            )
    if not lines:
        return ""
    return "### 🛰️ Systems\n" + "\n".join(lines) + "\n"
