from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sections:
    """The three named slots the review prompt keeps for context."""

    requirements: str | None = None
    knowledge: str | None = None
    impact: str | None = None


@dataclass(frozen=True)
class Told:
    """Context for the reviewer, as prose under a heading.

    Kept untyped so core needs no vocabulary for what a caller is passing.
    """

    heading: str
    body: str

    def rendered(self) -> str:
        return f"## {self.heading}\n\n{self.body}\n"
