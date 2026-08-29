from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .models import Change, Skill, SkillQuery, SkillResult, SkillVerdict


@runtime_checkable
class SkillSource(Protocol):
    """Somewhere skills are kept.

    A machine's own agent directories, a repository's checked-in ones, and a
    workspace's shared ones are three of these, and nothing above here has to
    tell them apart.
    """

    @property
    def origin(self) -> str: ...

    def read(self) -> tuple[Skill, ...]: ...


@runtime_checkable
class SkillReader(Protocol):
    def search(self, query: SkillQuery) -> SkillResult: ...

    def get(self, skill_id: str) -> Skill | None: ...


@runtime_checkable
class SkillSelector(Protocol):
    """Which of the skills on hand have anything to say about this change."""

    def select(
        self, skills: Sequence[Skill], change: Change, limit: int = 5
    ) -> tuple[Skill, ...]: ...


@runtime_checkable
class SkillChecker(Protocol):
    """Whether a change satisfies a skill."""

    def check(self, skill: Skill, change: Change) -> SkillVerdict: ...
