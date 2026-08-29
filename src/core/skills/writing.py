"""Writing a skill into the repository it is about.

Reading covers what a team already taught its coding agents. Writing covers the
rule somebody has in their head and nowhere else, which is most of them.

Only one place is ever written: the repository's own `.sourceant/skills`. What
sits in a person's agent folders is theirs, is frequently a link into a
repository of its own, and writing through one of those links would edit
somebody's other checkout without saying so. A rule about this code belongs
with this code anyway, where the rest of the team gets it by pulling.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .filesystem import MANIFEST, MAX_BODY
from .models import Skill

# Where a repository keeps the rules it states for itself.
OWN_SKILLS = Path(".sourceant") / "skills"

# A name, not a path. Anything that could climb out of the folder, or mean two
# things on two filesystems, is refused rather than sanitised: a rule saved
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

    The description is written on one line and quoted: it is the sentence that
    decides when the rule applies, and a line break in the middle of it would
    end the block early for a reader stricter than ours.
    """
    description = " ".join((skill.description or "").split())[:MAX_DESCRIPTION]
    quoted = description.replace("\\", "\\\\").replace('"', '\\"')
    body = (skill.body or "").strip()[:MAX_BODY]
    return (
        "---\n"
        f"name: {skill.name}\n"
        f'description: "{quoted}"\n'
        "---\n"
        f"\n{body}\n"
    )


def folder_for(root: Path, identifier: str) -> Path:
    return Path(root) / OWN_SKILLS / _checked(identifier)


def write_skill(root: Path, skill: Skill) -> Skill:
    """Record a rule in the repository it is about, and answer with what was written.

    The file is replaced rather than edited in place, so a reader that arrives
    mid-write sees the old rule or the new one and never half of each.
    """
    identifier = _checked(skill.id)
    name = (skill.name or identifier).strip()
    if not name:
        raise SkillWriteError("A rule needs a name")
    if not (skill.description or "").strip():
        raise SkillWriteError(
            "A rule needs a line saying when it applies, or nothing will pick it"
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
        origin="repository",
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
    """Forget a rule this repository stated. False when it had not stated one.

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
