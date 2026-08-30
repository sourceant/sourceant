"""Which skills have anything to say about a change.

A machine can easily hold thirty of these and most of them are about something
else. Asking a model to judge a change against all thirty costs thirty times
what asking it about the three that apply costs, and reads no better.

What the author said comes first, because it is a statement rather than a
guess. The format lets them write globs naming the files a skill is about, say
that only a person may invoke it, and record whatever else they like in a map
set aside for it, which is where this reads whether a skill belongs in a review
at all. Not everything somebody teaches an agent is about judging a change.

Where nobody has said, the choosing falls back to words: a skill says when to
use it, a change says what it touches, and where those overlap is where the
skill is worth reading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .matching import any_match
from .models import Change, Skill

# Letters rather than ASCII: skills and the prose around code are written
# in whatever language somebody works in, and matching on A to Z splits
# "médico" into "m" and "dico".
WORDS = re.compile(r"[^\W\d_][^\W_]+")

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

    def wanted(self, skill: Skill, change: Change) -> bool | None:
        """What the author said about this skill and this change, if anything.

        False where they said it is not for reviews, or that only a person may
        invoke it. True where they said it is, or wrote globs and the change
        touches one of the files. None where they said nothing, which is most
        of them.
        """
        said = skill.reviews
        if said is False:
            return False
        if not skill.automatic and said is not True:
            return False
        if said is True:
            return True
        if skill.paths:
            # Globs are the author narrowing their own skill. A change that
            # touches none of those files is one they already said it is not
            # about, so the wording is not consulted afterwards.
            return any_match(change.paths, skill.paths)
        return None

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

        stated: list[Skill] = []
        maybe: list[Skill] = []
        for skill in skills:
            said = self.wanted(skill, change)
            if said is False:
                continue
            (stated if said else maybe).append(skill)

        # What somebody stated comes first and is not competed with.
        chosen = sorted(stated, key=lambda skill: skill.id)[:limit]
        room = limit - len(chosen)
        if room <= 0 or not subject:
            return tuple(chosen)

        ranked = sorted(
            ((self.score(skill, subject), skill) for skill in maybe),
            key=lambda pair: (-pair[0][0], -pair[0][1], pair[1].id),
        )
        return (
            tuple(chosen)
            + tuple(skill for (matched, _), skill in ranked if matched >= self.minimum)[
                :room
            ]
        )
