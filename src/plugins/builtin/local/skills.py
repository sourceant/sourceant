"""Skills read from wherever they are already kept on this computer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from src.core.skills import (
    Catalogue,
    Skill,
    SkillWriteError,
    global_skills,
    remove_skill,
    repository_skills,
    sources_for,
    write_skill,
)

from .errors import Refused

# Both are kept beside the index, never in the covered checkout.
REPOSITORY = "repository"
GLOBAL = "global"

# One machine, one person. The scope has to be something, and anything derived
# from the machine would move the settings when a laptop is renamed.
from src.core.environment import LOCAL


def elsewhere() -> tuple[str, ...]:
    """Extra folders named in settings."""
    from src.core.settings.resolver import resolve

    try:
        named = str(resolve("skills.paths", user=LOCAL).value or "")
    except Exception:  # noqa: BLE001 - a store that cannot answer is no folders
        return ()
    return tuple(line.strip() for line in named.splitlines() if line.strip())


class SkillsOnDisk:
    """Skills from the agent directories, the repository, and any folder named
    in settings.

    All are read; only the global and repository stores are written to.
    """

    def __init__(self, repositories: Any) -> None:
        self._repositories = repositories

    def catalogue(self, workspace: str, repository: str = "") -> Catalogue:
        root = None
        if repository:
            root = Path(self._repositories.named(workspace, repository).path)
        ours = [(global_skills(), GLOBAL)]
        if repository:
            ours.append((repository_skills(repository), REPOSITORY))
        return Catalogue(sources=sources_for(root, elsewhere(), ours))

    def all(self, workspace: str, repository: str = "") -> Sequence[Skill]:
        return self.catalogue(workspace, repository).all()

    def one(
        self, workspace: str, identifier: str, repository: str = ""
    ) -> Skill | None:
        for skill in self.all(workspace, repository):
            if skill.id == identifier:
                return skill
        return None

    def write(
        self, workspace: str, skill: Skill, *, scope: str, repository: str = ""
    ) -> Skill:
        try:
            return write_skill(self._store(workspace, scope, repository), skill)
        except SkillWriteError as error:
            raise Refused(400, str(error)) from error

    def forget(
        self, workspace: str, identifier: str, *, scope: str, repository: str = ""
    ) -> bool:
        try:
            return remove_skill(self._store(workspace, scope, repository), identifier)
        except SkillWriteError as error:
            raise Refused(400, str(error)) from error

    def _store(self, workspace: str, scope: str, repository: str):
        """Where a writable skill is kept. Everything else is read-only."""
        if scope == GLOBAL:
            return global_skills()
        if scope != REPOSITORY:
            raise Refused(400, f"A skill is either {GLOBAL} or {REPOSITORY}")
        if not repository:
            raise Refused(400, "Name the repository this belongs to")
        # Checked, so a skill cannot be filed against an uncovered repository.
        self._repositories.named(workspace, repository)
        return repository_skills(repository)
