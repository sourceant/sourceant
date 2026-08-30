"""Reading skills off a disk.

A skill is a folder with a `SKILL.md` in it, and the file opens with a short
block naming the skill and saying when to use it. That is the shape the coding
agents already write, so anything a person has already taught their agent is
readable here without them moving or converting it.

A skill is also, just as often, one `<name>.md` on its own. Custom commands and
skills were merged: a file at `commands/deploy.md` and a folder at
`skills/deploy/SKILL.md` are the same thing to the agent that reads them, and a
person with forty of the first kind and none of the second has forty skills.
Both shapes are read. A loose `.md` beside a `SKILL.md` is not one of them: that
is the supporting material a skill was written with.

The block is YAML, and the format's own specification defines what may be in
it: a name and a description, globs saying which files the skill is about, a
free-form map a client may keep its own keys in, and a flag for guidance only a
person may invoke. Anything else an author wrote is carried through untouched.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .models import Skill

MANIFEST = "SKILL.md"

# What every one of these directories is called, whichever agent keeps it.
SKILLS = "skills"

# Deep enough for a plugin's skills to be nested under a plugin folder, shallow
# enough that pointing this at a home directory does not walk the whole disk.
MAX_DEPTH = 4

FENCE = "---"

# What the skill format defines and this reads. Anything else an author wrote is
# theirs and is carried through rather than interpreted.
KNOWN = frozenset(
    {"name", "description", "paths", "metadata", "disable-model-invocation"}
)

# Whatever is written past this is guidance for a person or a model to read, not
# something to send in full. What gets sent is decided by whoever asks.
MAX_BODY = 20_000

# A note about a folder of skills is not one of the skills in it.
NOT_A_SKILL = frozenset({"README", "CHANGELOG", "LICENCE", "LICENSE", "CONTRIBUTING"})


def read_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """The block at the top, and everything after it.

    A file without a block is all body: it still has a name, because its folder
    has one, and a skill with no description is simply one nothing will pick on
    its own. A block that is not readable YAML, or is not a mapping, is treated
    the same way, because half-reading somebody's file is worse than not
    reading it.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return {}, text

    end = None
    for number, line in enumerate(lines[1:], start=1):
        if line.strip() == FENCE:
            end = number
            break
    if end is None:
        return {}, text

    block = lines[1:end]
    body = "\n".join(lines[end + 1 :]).strip()
    try:
        fields = yaml.safe_load("\n".join(block))
    except yaml.YAMLError:
        fields = None
    if not isinstance(fields, dict):
        # Plenty of real frontmatter is not valid YAML. A hint written as
        # `[working | <path>] [--only]` is two flow sequences on one line,
        # which the agents that read these files tolerate and a parser does
        # not. Losing the name and the description over a field nothing here
        # reads would be the parser's fault, not the author's.
        fields = _plainly(block)

    return fields, body


def _plainly(lines: list[str]) -> dict[str, Any]:
    """Whatever `key: value` can be got out of a block YAML would not take.

    Only the shape every one of these files is written in: one key to a line,
    with an indented line continuing the value above it. Nothing nested, which
    is what a reader in this position could not have been sure of anyway.
    """
    fields: dict[str, Any] = {}
    key = ""
    for line in lines:
        if key and line[:1] in (" ", "\t") and line.strip():
            fields[key] = f"{fields[key]} {line.strip()}".strip()
            continue
        name, separator, value = line.partition(":")
        if not separator or not name.strip() or name.strip() != name.lstrip():
            continue
        key = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    return fields


def listed(value: Any) -> tuple[str, ...]:
    """A field the format lets somebody write either way.

    Globs and tool names are written as a YAML list or as one string with
    commas or spaces between them, and both mean the same thing.
    """
    if isinstance(value, str):
        return tuple(part for part in re.split(r"[,\s]+", value.strip()) if part)
    if isinstance(value, (list, tuple)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


@dataclass(frozen=True)
class DirectorySkillSource:
    """Every skill kept under one directory.

    Missing directories read as nothing rather than raising: most machines have
    some of these and no machine has all of them.
    """

    root: Path
    origin: str

    def _manifests(self, root: Path):
        """Every skill under a directory, in either shape, through symlinks.

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
            # A folder starting with a dot in a skills directory is the tool's
            # own: Codex keeps its built-ins in `.system`. Those are not a
            # team's rules, and reading them puts a page about generating
            # images in front of somebody's pull request.
            folders[:] = sorted(f for f in folders if not f.startswith("."))

            if MANIFEST in files:
                yield Path(here) / MANIFEST
                # A skill's folder holds one skill. Everything else in it, and
                # everything under it, is the reference material and the
                # scripts it was written with.
                folders[:] = []
                continue

            for name in sorted(files):
                if not name.endswith(".md") or name.startswith("."):
                    continue
                if Path(name).stem.upper() in NOT_A_SKILL:
                    continue
                yield Path(here) / name

    def read(self) -> tuple[Skill, ...]:
        root = Path(self.root).expanduser()
        if not root.is_dir():
            return ()

        skills: list[Skill] = []
        for manifest in sorted(self._manifests(root)):
            folder = manifest.parent
            alone = manifest.name != MANIFEST
            try:
                relative = (
                    manifest.with_suffix("").relative_to(root)
                    if alone
                    else folder.relative_to(root)
                )
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
            metadata = fields.get("metadata")
            skills.append(
                Skill(
                    id=identifier,
                    name=str(fields.get("name") or relative.name),
                    description=str(fields.get("description") or ""),
                    body=body[:MAX_BODY],
                    path=str(manifest),
                    origin=self.origin,
                    paths=listed(fields.get("paths")),
                    metadata=metadata if isinstance(metadata, dict) else {},
                    # The format's own way of saying only a person may start
                    # this. Nothing here chooses it on anybody's behalf.
                    automatic=not bool(fields.get("disable-model-invocation")),
                    properties={
                        key: value for key, value in fields.items() if key not in KNOWN
                    },
                )
            )
        return tuple(skills)


# A path to another document, as it is written in prose or in a link. Skills are
# routinely two lines long and point at the file that says the actual rule.
REFERENCE = re.compile(r"[`(\[\s]([\w./-]+\.md)[`)\]\s,.]", re.IGNORECASE)

# Enough for a rule spread over a handful of documents, and a stop well before
# somebody's whole knowledge base is sent to a model.
MAX_FOLLOWED = 3
MAX_FOLLOWED_BYTES = 24_000


def _skills_root(folder: Path) -> Path:
    """How far out of its own folder a skill may reach.

    Skills that share a rule keep it in a sibling folder, so the whole skills
    directory is in bounds. A skill kept somewhere with no such directory above
    it reaches no further than itself.
    """
    here = folder
    while here.parent != here:
        if here.name == SKILLS:
            return here
        here = here.parent
    return folder


def references(skill: Skill, limit: int = MAX_FOLLOWED) -> dict[str, str]:
    """The documents a skill points at, by where each one actually is.

    Most of these are a pointer: two lines saying to go and read the file that
    holds the rule. Judging a change against the pointer judges it against
    nothing, and the model says so, which reads as the change being at fault.

    Keyed by resolved path rather than by what the skill called it, so a
    document several rules point at is recognisably the same document.

    Only what the skill names, only one level down, and only inside the folder
    the skill came from. A rule is not a licence to read the rest of the disk.
    """
    if not skill.path:
        return {}

    here = Path(skill.path).parent
    try:
        boundary = _skills_root(here.resolve())
    except OSError:
        return {}

    found: dict[str, str] = {}
    spent = 0
    for name in REFERENCE.findall(f" {skill.body} "):
        if len(found) >= limit or spent >= MAX_FOLLOWED_BYTES:
            break
        try:
            target = (here / name).resolve()
        except OSError:
            continue
        if str(target) in found or not target.is_file():
            continue
        # Outside the folder the skills live in is somebody else's document.
        if boundary != target and boundary not in target.parents:
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        text = text[: MAX_FOLLOWED_BYTES - spent]
        spent += len(text)
        found[str(target)] = text

    return found


def attach(body: str, documents: dict[str, str]) -> str:
    """A skill's own words with the documents it points at written out under them."""
    return body + "".join(
        f"\n\n--- {Path(path).name} ---\n\n{text}" for path, text in documents.items()
    )


def followed(skill: Skill, limit: int = MAX_FOLLOWED) -> str:
    """A skill's own words plus everything it points at, as one document."""
    return attach(skill.body, references(skill, limit))


# What a folder of skills is called, whatever keeps it. These are roles rather
# than products: an agent puts its skills in `skills`, and the custom commands
# that were merged into skills in `commands` or `prompts`.
ROLES: tuple[str, ...] = ("skills", "commands", "prompts")

# Somewhere somebody named that discovery would not have found.
ELSEWHERE = "elsewhere"

# Whose machine this is. A core running in a container has a home of its own,
# and nothing a person taught their coding agent is in it, so the home to read
# is named rather than assumed.
MACHINE_HOME = "SOURCEANT_MACHINE_HOME"

# Dot directories that are nobody's skills and are expensive to look inside.
NOT_A_TOOL = frozenset({".git", ".cache", ".npm", ".venv", ".tox", ".idea", ".vscode"})


def machine_home() -> Path:
    return Path(os.environ.get(MACHINE_HOME) or Path.home())


def discover(root: Path) -> list[DirectorySkillSource]:
    """Every folder of skills under a root, and which tool keeps each one.

    Named lists go stale. The skill format is an open standard that upwards of
    thirty tools have adopted, each with a dot directory of its own, and a list
    of the ones known on the day this was written would be missing whichever
    one somebody installs next.

    So it is found instead: a dot directory is a tool, and a folder inside it
    with a skills-shaped name is that tool's skills. The name of the tool falls
    out of the directory, so one nobody has heard of works with no change here.
    """
    try:
        here = sorted(entry for entry in root.iterdir() if entry.name.startswith("."))
    except OSError:
        return []

    found: list[DirectorySkillSource] = []
    for tool in here:
        if tool.name in NOT_A_TOOL or not tool.is_dir():
            continue
        for role in ROLES:
            folder = tool / role
            if folder.is_dir():
                found.append(DirectorySkillSource(folder, tool.name.lstrip(".")))
    return found


def sources_for(
    root: Path | None = None,
    extra: Iterable[str] = (),
    ours: Iterable[tuple[Path, str]] = (),
) -> tuple[DirectorySkillSource, ...]:
    """Everywhere worth looking, for a machine and optionally a repository.

    What the coding agents keep is found rather than listed, so a tool nobody
    here has heard of is read the same as the ones they have. A repository's
    own come after the machine's, so a project can say something that departs
    from how somebody usually works and be seen saying it.

    What was written here comes last, because it is the one anybody can change
    from this screen and so is the one that should win.

    Somewhere discovery would not reach, a checkout of skills kept off on its
    own, is named by whoever knows about it. A named folder is read relative to
    the repository when it is relative and as written when it is not, which is
    how people write both.
    """
    found = discover(machine_home())
    if root is not None:
        found += discover(Path(root))

    for named in extra:
        named = (named or "").strip()
        if not named:
            continue
        where = Path(named).expanduser()
        if not where.is_absolute() and root is not None:
            where = Path(root) / where
        found.append(DirectorySkillSource(where, ELSEWHERE))

    found += [DirectorySkillSource(where, origin) for where, origin in ours]
    return tuple(found)
