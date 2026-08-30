"""Several places skills are kept, read as one list.

Ids collide across places, so the later source wins: a repository's own skill
overrides the machine's skill of the same name, which is how a project says
something its machine does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .interfaces import SkillSource
from .models import Skill, SkillQuery, SkillResult


def _matches(skill: Skill, text: str) -> bool:
    if not text:
        return True
    wanted = text.lower()
    return (
        wanted in skill.id.lower()
        or wanted in skill.name.lower()
        or wanted in skill.description.lower()
    )


@dataclass
class Catalogue:
    sources: Sequence[SkillSource] = field(default_factory=tuple)

    def all(self) -> tuple[Skill, ...]:
        by_id: dict[str, Skill] = {}
        for source in self.sources:
            for skill in source.read():
                by_id[skill.id] = skill
        return tuple(by_id.values())

    def search(self, query: SkillQuery) -> SkillResult:
        found = [
            skill
            for skill in self.all()
            if (not query.origins or skill.origin in query.origins)
            and _matches(skill, query.text)
        ]
        return SkillResult(skills=tuple(found[: query.limit]), total=len(found))

    def get(self, skill_id: str) -> Skill | None:
        for skill in self.all():
            if skill.id == skill_id:
                return skill
        return None
