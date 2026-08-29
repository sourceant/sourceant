"""Writing a skill down, in the repository or on the machine.

Reading covers what a team already taught its coding agents. Writing covers
what somebody has in their head and nowhere else, which is most of it.

Two places are written, and both are ours: a repository's `.sourceant/skills`,
where the rest of the team gets it by pulling, and the machine's own
`~/.sourceant/skills`, for what somebody wants everywhere rather than in one
project. What sits in the folders named after a coding agent is that agent's,
is frequently a link into a checkout of its own, and writing through one of
those links would edit somebody's other repository without saying so.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from .filesystem import MANIFEST, MAX_BODY
from .models import Skill

# Where a repository, or a machine, keeps what it states for itself.
OWN_SKILLS = Path(".sourceant") / "skills"

# A name, not a path. Anything that could climb out of the folder, or mean two
# things on two filesystems, is refused rather than sanitised: a skill saved
# under a name nobody asked for is worse than one that was not saved.
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

MAX_DESCRIPTION = 1000


class SkillWriteError(ValueError):
    pass


def _checked(identifier: str) -> str:
    identifier = (identifier or "").strip().lower()
    if not NAME.match(identifier):
        raise SkillWriteError(
            "A name is lower case words joined by hyphens, like 'retry-limit'"
        )
    return identifier


def _rendered(skill: Skill) -> str:
    """The file, in the shape every coding agent already reads.

    Written as YAML, in the order somebody reading it would want: what it is
    called, when it applies, then whatever narrows it. The description is put
    on one line, because it is the sentence that decides when the skill
    applies and a break in the middle of it ends the block early for a reader
    stricter than ours.

    Anything the author had written that this does not act on is written back
    unchanged. Dropping somebody's `license` because this had no opinion about
    it would be losing their work.
    """
    fields: dict[str, object] = {
        "name": skill.name,
        "description": " ".join((skill.description or "").split())[:MAX_DESCRIPTION],
    }
    if skill.paths:
        fields["paths"] = list(skill.paths)
    if skill.metadata:
        fields["metadata"] = dict(skill.metadata)
    if not skill.automatic:
        fields["disable-model-invocation"] = True
    fields.update(
        {key: value for key, value in skill.properties.items() if key not in fields}
    )

    block = yaml.safe_dump(
        fields,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=10_000,
    )
    body = (skill.body or "").strip()[:MAX_BODY]
    return f"---\n{block}---\n\n{body}\n"


def folder_for(root: Path, identifier: str) -> Path:
    return Path(root) / OWN_SKILLS / _checked(identifier)


def write_skill(root: Path, skill: Skill, origin: str = "repository") -> Skill:
    """Write a skill under a root, and answer with what was written.

    The file is replaced rather than edited in place, so a reader that arrives
    mid-write sees the old rule or the new one and never half of each.
    """
    identifier = _checked(skill.id)
    name = (skill.name or identifier).strip()
    if not name:
        raise SkillWriteError("A skill needs a name")
    if not (skill.description or "").strip():
        raise SkillWriteError(
            "A skill needs a line saying when it applies, or nothing will pick it"
        )

    folder = folder_for(root, identifier)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MANIFEST

    written = Skill(
        id=identifier,
        name=name,
        description=" ".join(skill.description.split())[:MAX_DESCRIPTION],
        body=(skill.body or "").strip()[:MAX_BODY],
        path=str(target),
        origin=origin,
        paths=skill.paths,
        metadata=dict(skill.metadata),
        automatic=skill.automatic,
        properties=dict(skill.properties),
    )

    handle, temporary = tempfile.mkstemp(dir=folder, prefix=".skill-", suffix=".md")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(_rendered(written))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

    return written


def remove_skill(root: Path, identifier: str) -> bool:
    """Forget a skill written under a root. False when there was none.

    Only the manifest and the folder holding it go. Anything else somebody put
    in there is theirs, and a folder that still has something in it stays.
    """
    folder = folder_for(root, identifier)
    manifest = folder / MANIFEST
    if not manifest.is_file():
        return False
    manifest.unlink()
    try:
        folder.rmdir()
    except OSError:
        pass
    return True
