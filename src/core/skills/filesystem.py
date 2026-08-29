"""Reading skills off a disk.

A skill is a folder with a `SKILL.md` in it, and the file opens with a short
block naming the skill and saying when to use it. That is the shape the coding
agents already write, so anything a person has already taught their agent is
readable here without them moving or converting it.

The block is read with a small parser rather than a YAML one. It only ever
carries a name and a description, and a dependency for two keys buys nothing;
anything more elaborate in there is carried through untouched as a property
rather than interpreted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import Skill

MANIFEST = "SKILL.md"

# Deep enough for a plugin's skills to be nested under a plugin folder, shallow
# enough that pointing this at a home directory does not walk the whole disk.
MAX_DEPTH = 4

FENCE = "---"

# Whatever is written past this is guidance for a person or a model to read, not
# something to send in full. What gets sent is decided by whoever asks.
MAX_BODY = 20_000


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_front_matter(text: str) -> tuple[dict[str, str], str]:
    """The block at the top, and everything after it.

    A file without a block is all body: it still has a name, because its folder
    has one, and a skill with no description is simply one nothing will pick on
    its own.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return {}, text

    fields: dict[str, str] = {}
    key = ""
    end = len(lines)
    for number, line in enumerate(lines[1:], start=1):
        if line.strip() == FENCE:
            end = number
            break
        # An indented line continues the value above it, which is how a long
        # description gets written without one enormous line.
        if key and line[:1] in (" ", "\t"):
            fields[key] = f"{fields[key]} {line.strip()}".strip()
            continue
        name, separator, value = line.partition(":")
        if not separator:
            continue
        key = name.strip()
        fields[key] = _unquote(value)

    return fields, "\n".join(lines[end + 1 :]).strip()


@dataclass(frozen=True)
class DirectorySkillSource:
    """Every skill kept under one directory.

    Missing directories read as nothing rather than raising: most machines have
    some of these and no machine has all of them.
    """

    root: Path
    origin: str

    def _manifests(self, root: Path):
        """Every SKILL.md under a directory, through symlinks.

        People keep their skills in a repository of their own and link them
        into the agent's folder, so a walk that does not follow links finds
        almost none of them. Following links means a link back up the tree
        would walk forever, so somewhere already visited is not visited again.
        """
        seen: set[str] = set()
        for here, folders, files in os.walk(root, followlinks=True):
            real = os.path.realpath(here)
            if real in seen:
                folders[:] = []
                continue
            seen.add(real)

            depth = len(Path(here).relative_to(root).parts)
            if depth >= MAX_DEPTH:
                folders[:] = []
            folders[:] = sorted(f for f in folders if not f.startswith(".git"))

            if MANIFEST in files:
                yield Path(here) / MANIFEST

    def read(self) -> tuple[Skill, ...]:
        root = Path(self.root).expanduser()
        if not root.is_dir():
            return ()

        skills: list[Skill] = []
        for manifest in sorted(self._manifests(root)):
            folder = manifest.parent
            try:
                relative = folder.relative_to(root)
            except ValueError:
                continue
            if len(relative.parts) > MAX_DEPTH or not relative.parts:
                continue
            try:
                text = manifest.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            fields, body = read_front_matter(text)
            identifier = relative.as_posix()
            extra = {
                key: value
                for key, value in fields.items()
                if key not in ("name", "description")
            }
            skills.append(
                Skill(
                    id=identifier,
                    name=fields.get("name") or folder.name,
                    description=fields.get("description", ""),
                    body=body[:MAX_BODY],
                    path=str(manifest),
                    origin=self.origin,
                    properties=extra,
                )
            )
        return tuple(skills)


# Where the coding agents keep them, on a machine and inside a repository. A
# repository's own come last so a project can say something its machine does
# not, and be seen saying it.
MACHINE_SKILLS: tuple[tuple[str, str], ...] = (
    (".claude/skills", "claude"),
    (".codex/skills", "codex"),
)

REPOSITORY_SKILLS: tuple[tuple[str, str], ...] = (
    (".claude/skills", "repository"),
    (".codex/skills", "repository"),
    (".sourceant/skills", "repository"),
)

# Whose machine this is. A core running in a container has a home of its own,
# and nothing a person taught their coding agent is in it, so the home to read
# is named rather than assumed.
MACHINE_HOME = "SOURCEANT_MACHINE_HOME"


def machine_home() -> Path:
    return Path(os.environ.get(MACHINE_HOME) or Path.home())


def sources_for(root: Path | None = None) -> tuple[DirectorySkillSource, ...]:
    """Everywhere worth looking, for a machine and optionally a repository."""
    home = machine_home()
    found = [
        DirectorySkillSource(home / path, origin) for path, origin in MACHINE_SKILLS
    ]
    if root is not None:
        found += [
            DirectorySkillSource(Path(root) / path, origin)
            for path, origin in REPOSITORY_SKILLS
        ]
    return tuple(found)
