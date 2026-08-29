from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# What a skill is allowed to say about a change. A skill that only advises is
# read and reported; a skill that blocks is a reason a change is not ready.
ADVISORY = "advisory"
BLOCKING = "blocking"
SEVERITIES = frozenset({ADVISORY, BLOCKING})


@dataclass(frozen=True)
class Skill:
    """A named piece of guidance somebody wrote down for whatever reads code.

    The id is unique within the place it came from, not across places: two
    machines can both keep a `commit` skill and mean different things by it.
    """

    id: str
    name: str
    description: str
    body: str
    path: str = ""
    origin: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("a skill needs an id and a name")


@dataclass(frozen=True)
class SkillQuery:
    origins: tuple[str, ...] = ()
    text: str = ""
    limit: int = 50

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 500:
            raise ValueError("limit must be between 1 and 500")


@dataclass(frozen=True)
class SkillResult:
    skills: tuple[Skill, ...] = ()
    total: int = 0


@dataclass(frozen=True)
class Change:
    """What is being judged, in the terms a skill is written about.

    A working tree, a pull request and a single commit all reduce to this, so
    nothing downstream has to know which it was.
    """

    title: str = ""
    description: str = ""
    paths: tuple[str, ...] = ()
    diff: str = ""


@dataclass(frozen=True)
class SkillFinding:
    detail: str
    severity: str = ADVISORY
    path: str = ""
    line: int | None = None

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(sorted(SEVERITIES))}")


@dataclass(frozen=True)
class SkillVerdict:
    """Whether a change satisfies one skill, and what to do when it does not."""

    skill_id: str
    passed: bool
    findings: tuple[SkillFinding, ...] = ()
    note: str = ""

    @property
    def blocking(self) -> tuple[SkillFinding, ...]:
        return tuple(f for f in self.findings if f.severity == BLOCKING)
