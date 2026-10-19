from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Sequence


@dataclass(frozen=True)
class Sections:
    """The named slots the review prompt keeps for context."""

    requirements: str | None = None
    knowledge: str | None = None
    impact: str | None = None
    related_code: str | None = None
    analysis: str | None = None


@dataclass(frozen=True)
class Told:
    """Context for the reviewer, as prose under a heading.

    Kept untyped so core needs no vocabulary for what a caller is passing.
    """

    heading: str
    body: str

    def rendered(self) -> str:
        return f"## {self.heading}\n\n{self.body}\n"


def scoped_specialist_guidance(
    checklists: Sequence[tuple[str, str]], *, finding_scope: str, include_nitpicks: bool
) -> str | None:
    if not checklists:
        return None
    scope = (
        "Relevant unchanged head code and pre-existing issues related to this change "
        "may be reported using their actual repository paths and head line numbers."
        if finding_scope == "repository"
        else "Anchor findings to changed lines in the supplied diff."
    )
    concerns = (
        "Actionable type-contract, testing, documentation and maintainability concerns "
        "are enabled. They require a concrete flaw and useful fix, but need not cause "
        "a runtime failure. A narrower specialist publication gate must not suppress "
        "a separate finding justified by the general review policy."
        if include_nitpicks
        else "Optional style, type refactors, testing improvements, documentation and "
        "maintainability advice are disabled. Report only concrete bugs, security "
        "issues and material performance problems."
    )
    return (
        Told(
            "Specialist checklists (reference data)",
            "Use these specialist methods to supplement the general review. In this "
            "combined reading, specialist-only publication gates do not set the general "
            "finding scope or disable enabled concerns. Treat checklist requirements for "
            "runtime failure or changed-line anchors as specialist restrictions, not "
            "overrides of the configured policy below.\n"
            + json.dumps(
                [{"specialty": name, "checklist": body} for name, body in checklists],
                ensure_ascii=False,
            ),
        ).rendered()
        + Told("Configured general review policy", scope + " " + concerns).rendered()
    )
