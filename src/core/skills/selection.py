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
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.utils.logger import logger

from .matching import any_match
from .models import REVIEW, Change, Skill, SkillScope, SkillType


def said_here(value: Any) -> dict[str, dict[str, bool]]:
    """What this scope says a skill is for, out of the setting that holds it.

    Anything the wrong shape is dropped rather than raised on: a setting
    somebody hand-edited badly should not stop a review.
    """
    if not isinstance(value, Mapping):
        return {}
    said: dict[str, dict[str, bool]] = {}
    for identifier, uses in value.items():
        if not isinstance(identifier, str) or not isinstance(uses, Mapping):
            continue
        kept = {
            use: answer
            for use, answer in uses.items()
            if isinstance(use, str) and isinstance(answer, bool)
        }
        if kept:
            said[identifier.strip()] = kept
    return said


def for_purpose(
    skills: Sequence[Skill],
    change: Change,
    purpose: str = REVIEW,
    expert_passes: str = "auto",
    limit: int = 5,
    said: Mapping[str, Mapping[str, bool]] | None = None,
    ask: Any = None,
    model: str = "",
) -> tuple[Skill, ...]:
    """The skills to read against this change, for one of the things we do.

    Anything answered for is honoured first and costs nothing to decide. What
    nobody has answered for is a question about relevance, which a model answers
    better than shared words do, so it is asked where one is going to be asked
    anyway. Without a model, or where the call fails, the words decide.
    """
    selector = PhraseSkillSelector(purpose=purpose, said=said or {})

    def fill(candidates: Sequence[Skill], room: int) -> tuple[Skill, ...]:
        if room <= 0 or not candidates:
            return ()
        if ask is not None:
            answered = asked_for(candidates, change, ask, model, limit=room)
            if answered:
                return answered
        return selector.ranked(candidates, change, room)

    if not isinstance(expert_passes, str) or expert_passes.strip() == "auto":
        stated, maybe = selector.sift(skills, change)
        chosen = stated[:limit]
        return chosen + fill(maybe, limit - len(chosen))
    requested = tuple(dict.fromkeys(expert_passes.split()))
    experts = {
        skill.id: skill for skill in skills if skill.type == SkillType.REVIEW_PASS
    }
    missing = [identifier for identifier in requested if identifier not in experts]
    if missing:
        raise ValueError("Unknown expert review passes: " + ", ".join(missing))
    stated, maybe = selector.sift(
        tuple(skill for skill in skills if skill.type != SkillType.REVIEW_PASS), change
    )
    guidance = stated[:limit] + fill(maybe, limit - len(stated[:limit]))
    # A pass somebody asked for by name is run unless it is turned off here, and
    # one turned on here is run whether or not the list names it. Guidance is
    # already answered for by the selector; a pass is chosen by name.
    named = tuple(
        experts[identifier]
        for identifier in requested
        if selector.answer(experts[identifier]) is not False
    )
    # Only what this scope insisted on. A pass whose own file says it is for
    # reviews is still one the list chooses between: naming passes is how a
    # repository narrows them.
    insisted = tuple(
        skill
        for identifier, skill in experts.items()
        if identifier not in requested and selector.demanded(skill) is True
    )
    return (*guidance, *named, *insisted)


def for_review(
    skills: Sequence[Skill],
    change: Change,
    expert_passes: str = "auto",
    limit: int = 5,
    said: Mapping[str, Mapping[str, bool]] | None = None,
    ask: Any = None,
    model: str = "",
) -> tuple[Skill, ...]:
    """The skills a review reads, which is the one purpose acted on today."""
    return for_purpose(
        skills,
        change,
        purpose=REVIEW,
        expert_passes=expert_passes,
        limit=limit,
        said=said,
        ask=ask,
        model=model,
    )


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
    #: The thing being done, since a skill can be for several.
    purpose: str = REVIEW
    #: What this scope said a skill is for, which outranks what the author said.
    #: A skill somebody else owns cannot be edited here, so this is the only way
    #: to answer for one.
    said: Mapping[str, Mapping[str, bool]] = field(default_factory=dict)

    def answer(self, skill: Skill) -> bool | None:
        """Whether this skill is for this purpose: here first, then its file."""
        mine = self.demanded(skill)
        if mine is not None:
            return mine
        return skill.applies_to(self.purpose)

    def demanded(self, skill: Skill) -> bool | None:
        """What this scope said about it, which is not what its author said."""
        return self.said.get(skill.id, {}).get(self.purpose)

    def wanted(self, skill: Skill, change: Change) -> bool | None:
        """Whether this skill belongs in this reading, if anybody has said.

        False where it is not for this purpose, or where only a person may
        invoke it; True where it is, or where globs were written and the change
        touches one of the files; None where nobody said anything, which is most
        of them.
        """
        said = self.answer(skill)
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

    def sift(
        self, skills: Sequence[Skill], change: Change
    ) -> tuple[tuple[Skill, ...], tuple[Skill, ...]]:
        """What somebody stated applies, and what nobody has answered for.

        What was stated comes first and is not competed with. What was vetoed is
        gone. The rest is a question for whoever can answer it.
        """
        stated: list[Skill] = []
        maybe: list[Skill] = []
        for skill in skills:
            said = self.wanted(skill, change)
            if said is False:
                continue
            (stated if said else maybe).append(skill)
        stated.sort(
            key=lambda skill: (skill.origin != SkillScope.SYSTEM.value, skill.id)
        )
        return tuple(stated), tuple(maybe)

    def ranked(
        self, skills: Sequence[Skill], change: Change, limit: int
    ) -> tuple[Skill, ...]:
        """The ones sharing most with what the change says it is about.

        What it says, not its paths: a path offers `api`, `server`, `model`,
        `type` and the language's own extension, which is every generic word
        there is, and one of them matching a skill about spreadsheets is not a
        reason to read it. A skill that is about files says so with globs.
        """
        subject = words(f"{change.title} {change.description}")
        if limit <= 0 or not subject:
            return ()
        ranked = sorted(
            ((self.score(skill, subject), skill) for skill in skills),
            key=lambda pair: (-pair[0][0], -pair[0][1], pair[1].id),
        )
        return tuple(
            skill for (matched, _), skill in ranked if matched >= self.minimum
        )[:limit]

    def select(
        self, skills: Sequence[Skill], change: Change, limit: int = 5
    ) -> tuple[Skill, ...]:
        stated, maybe = self.sift(skills, change)
        chosen = stated[:limit]
        return chosen + self.ranked(maybe, change, limit - len(chosen))


# How many skills a model is shown at once. A machine with a hundred of them
# still fits, because only the name and the sentence saying when each applies
# are sent, never the body.
OFFERED = 200


def asked_for(
    skills: Sequence[Skill],
    change: Change,
    ask: Any,
    model: str = "",
    limit: int = 5,
) -> tuple[Skill, ...]:
    """The skills a model says bear on this change, or () where it cannot say.

    Wording alone answers badly in both directions: a skill about migrations and
    a change about migrations find each other, and a skill about slide decks and
    a change touching a file called `model.go` also find each other. A model is
    shown what each skill says it is for and asked which of them apply.

    Anything it answers with that was not offered is dropped, so a hallucinated
    id cannot pull in a skill nobody has.
    """
    offered = tuple(skills)[:OFFERED]
    if not offered or ask is None:
        return ()
    listed = "\n".join(
        f"- {skill.id}: {skill.name}. {skill.description}" for skill in offered
    )
    files = "\n".join(f"- {path}" for path in tuple(change.paths)[:60])
    prompt = (
        "A change is about to be reviewed. Which of these skills bear on it?\n\n"
        f"Title: {change.title or '(none given)'}\n"
        f"Description: {change.description or '(none given)'}\n"
        f"Files:\n{files or '- (none)'}\n\n"
        f"Skills:\n{listed}\n\n"
        f"Answer with the ids of at most {limit} that apply, one a line, and "
        "nothing else. Answer with nothing at all where none of them applies. "
        "A skill applies when what it says it is for is what this change is "
        "about, not because a word appears in both."
    )
    try:
        answered = ask(prompt, purpose="skill-selection") if model else ask(prompt)
    except Exception as error:  # noqa: BLE001 - whatever a provider raises
        logger.warning(f"Choosing skills without a model: {error}")
        return ()
    held = {skill.id: skill for skill in offered}
    wanted: list[Skill] = []
    for line in str(answered or "").splitlines():
        identifier = line.strip().strip("-*` ").split(":")[0].strip()
        skill = held.get(identifier)
        if skill is not None and skill not in wanted:
            wanted.append(skill)
    return tuple(wanted[:limit])
