"""Which skills have anything to say about a change.

A machine can easily hold thirty of these and most of them are about something
else. Asking a model to judge a change against all thirty costs thirty times
what asking it about the three that apply costs, and reads no better.

The choosing is done on words, deterministically, before anything is asked.
A skill says when to use it; a change says what it touches. Where those overlap
is where the skill is worth reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .models import Change, Skill

WORDS = re.compile(r"[A-Za-z][A-Za-z0-9]+")

# Ordinary English, and the sentence every skill description opens with. Both
# appear in every skill and every change, so counting them would rank on
# nothing. Words that carry a subject stay: a skill about migrations and a
# change touching migrations should find each other on that word.
COMMON = frozenset(
    {
        "the",
        "and",
        "for",
        "when",
        "with",
        "this",
        "that",
        "into",
        "from",
        "any",
        "are",
        "not",
        "use",
        "uses",
        "used",
        "using",
        "user",
        "ask",
        "asks",
        "asked",
        "want",
        "wants",
        "should",
        "invoke",
        "invokes",
        "mention",
        "mentions",
        "follow",
        "following",
        "work",
        "working",
        "code",
        "codebase",
        "file",
        "files",
        "src",
        "app",
        "lib",
    }
)

MIN_SCORE = 1


def words(text: str) -> set[str]:
    """The words of a phrase, singular and plural counted as the same word.

    A skill about a migration and a change about migrations are about the same
    thing, and a matcher that cannot see that misses most of what it is for.
    Both forms are kept, so the comparison works whichever way round they were
    written.
    """
    found: set[str] = set()
    for word in WORDS.findall(text or ""):
        word = word.lower()
        found.add(word)
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            found.add(word[:-1])
    return found - COMMON


@dataclass(frozen=True)
class PhraseSkillSelector:
    """Overlap between what a skill says it is for and what a change touches.

    Only the name and the description are read. A skill's description is the
    sentence its author wrote to say when it applies, which is exactly this
    question; its body is a document, and a long document shares words with
    every change there has ever been. Counting the body ranked a page about
    generating images above the team's own commit rules.
    """

    minimum: int = MIN_SCORE

    def score(self, skill: Skill, subject: set[str]) -> tuple[int, float]:
        """How many words a skill shares with a change, and how much of it that is.

        The count first: two shared subjects beat one. Then the share of the
        description those words are, so a skill that says one thing and matches
        it beats one that says twenty things and happens to mention this.
        """
        described = words(f"{skill.name} {skill.description}")
        if not described:
            return 0, 0.0
        matched = described & subject
        return len(matched), len(matched) / len(described)

    def select(
        self, skills: Sequence[Skill], change: Change, limit: int = 5
    ) -> tuple[Skill, ...]:
        subject = words(
            " ".join(
                (
                    change.title,
                    change.description,
                    " ".join(re.split(r"[/\\._-]+", " ".join(change.paths))),
                )
            )
        )
        if not subject:
            return ()
        ranked = sorted(
            ((self.score(skill, subject), skill) for skill in skills),
            key=lambda pair: (-pair[0][0], -pair[0][1], pair[1].id),
        )
        return tuple(
            skill for (matches, _), skill in ranked if matches >= self.minimum
        )[:limit]
