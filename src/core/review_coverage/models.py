"""What a review was able to read, and what it was not.

A review that could not reach a repository and one that reached it and found
nothing say the same thing today: nothing. The difference matters more than
most findings do, because the second is an answer and the first is a gap
wearing an answer's clothes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Which repositories the change reaches.
REACH = "reach"
#: What, in a repository the change reaches, bears on it.
SIBLING_SOURCE = "sibling source"
#: What the changed code itself connects to.
NEIGHBOURING_CODE = "neighbouring code"
#: What the team wrote down about work here.
SKILLS = "skills"

#: A walk of the system graph from where the changed files sit.
GRAPH = "graph"
#: The graph's own shape, where nothing had recorded where a file belongs.
PREFIX = "folder"
#: Keyword search of a checkout.
KEYWORD = "keyword search"
#: Keyword search of the source kept when a repository was read.
SNAPSHOT = "kept source"
#: The structural index.
INDEX = "index"
#: The change itself, which is always there.
DIFF = "diff"


@dataclass(frozen=True)
class Attempt:
    """One source asked one question, and what came back."""

    question: str
    method: str
    answered: bool
    #: The repository the question was about, where it was about one.
    target: str = ""
    #: Why nothing came back, in the words of whatever refused.
    reason: str = ""


class Coverage:
    """What was asked, of what, and what answered.

    Collected while a review is assembled and kept with it, so a reader can
    tell a quiet review from a blind one, and a test can assert that the
    reading happened rather than that it happened to find something.
    """

    def __init__(self) -> None:
        self._attempts: list[Attempt] = []
        self._reached: tuple[str, ...] = ()

    def record(
        self,
        question: str,
        method: str,
        *,
        answered: bool,
        target: str = "",
        reason: str = "",
    ) -> None:
        self._attempts.append(Attempt(question, method, answered, target, reason))

    def reaches(self, repositories: tuple[str, ...]) -> None:
        """The repositories the walk said this change reaches."""
        self._reached = tuple(sorted(set(repositories)))

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        return tuple(self._attempts)

    @property
    def reached(self) -> tuple[str, ...]:
        return self._reached

    def answered(self, question: str, target: str = "") -> bool:
        return any(
            attempt.answered
            and attempt.question == question
            and (not target or attempt.target == target)
            for attempt in self._attempts
        )

    @property
    def unread(self) -> tuple[tuple[str, str], ...]:
        """Repositories the change reaches whose source nobody could read.

        A repository is unread when every attempt at its source failed. The
        reason kept is the first one given, which is the one nearest to what
        actually stopped it.

        Anything asked about counts, not only what the walk named. A
        repository can be asked about before the walk has said anything, and
        one asked about and unanswered is unread either way.
        """
        asked = {
            attempt.target
            for attempt in self._attempts
            if attempt.question == SIBLING_SOURCE and attempt.target
        }
        missing = []
        for repository in sorted(set(self._reached) | asked):
            if self.answered(SIBLING_SOURCE, repository):
                continue
            reason = next(
                (
                    attempt.reason
                    for attempt in self._attempts
                    if attempt.target == repository and attempt.reason
                ),
                "not read",
            )
            missing.append((repository, reason))
        return tuple(missing)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reached": list(self._reached),
            "unread": [
                {"repository": name, "reason": reason} for name, reason in self.unread
            ],
            "attempts": [
                {
                    "question": attempt.question,
                    "method": attempt.method,
                    "answered": attempt.answered,
                    "target": attempt.target,
                    "reason": attempt.reason,
                }
                for attempt in self._attempts
            ],
        }
