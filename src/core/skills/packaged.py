from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import PurePosixPath

from .filesystem import skill_from_markdown


@dataclass(frozen=True)
class PackagedSkillSource:
    package: str
    prefix: str
    origin: str

    def read(self):
        resources = import_module(self.package + "._compiled_resources").resources()
        skills = []
        for name, content in sorted(resources.items()):
            if not name.startswith(self.prefix + "/") or not name.endswith("/SKILL.md"):
                continue
            identifier = str(PurePosixPath(name).parent.relative_to(self.prefix))
            skills.append(
                skill_from_markdown(
                    content.decode("utf-8"), identifier, origin=self.origin
                )
            )
        return tuple(skills)
