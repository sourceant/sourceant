"""Reading what a repository already says about itself.

Most repositories have written some of this down: an architecture decision
record, a conventions section in a contributing guide, a page of rules for
whatever agent works on them. It is knowledge already; it is just not anywhere a
tool can reach.

This reads those files and nothing else. It does not summarise, infer, or
decide: every object it makes points at the heading it came from, so a person
can check it against the source in one step. Judging whether something is worth
recording, or true, is a different job and needs a different kind of reader.

What it produces is a starting point, and is marked as one: everything comes
back `proposed` rather than `accepted`, because nobody has agreed to it yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import KnowledgeObject

# Where projects write these down. Ordered, so a repository with several is read
# in the order somebody would think of them.
SOURCES = (
    "docs/adr",
    "docs/adrs",
    "docs/decisions",
    "docs/architecture/decisions",
    "adr",
    "AGENTS.md",
    "CLAUDE.md",
    "CONVENTIONS.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "README.md",
)

# A heading only becomes knowledge when it says what kind it is. Anything else
# is prose about the project, and recording it would fill the store with
# headings like "Installation".
KINDS = {
    "decision": "decision",
    "decisions": "decision",
    "convention": "convention",
    "conventions": "convention",
    "constraint": "constraint",
    "constraints": "constraint",
    "pattern": "pattern",
    "patterns": "pattern",
    "workaround": "workaround",
    "workarounds": "workaround",
    "rule": "convention",
    "rules": "convention",
    "principle": "convention",
    "principles": "convention",
    "trade-off": "decision",
    "tradeoffs": "decision",
}

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
ADR_NAME = re.compile(r"^(?:\d+[-_])?(.+)\.md$", re.IGNORECASE)
MAX_SUMMARY = 300


@dataclass(frozen=True)
class Seed:
    """One thing a repository states, and where it states it."""

    knowledge: KnowledgeObject
    path: str


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _shorten(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_SUMMARY:
        return collapsed
    return collapsed[: MAX_SUMMARY - 1].rsplit(" ", 1)[0] + "…"


def _paragraph(body: str) -> str:
    for block in body.split("\n\n"):
        text = " ".join(block.split())
        if text and not text.startswith(("#", "|", "```")):
            return _shorten(text)
    return ""


def _sections(text: str) -> list[tuple[str, str]]:
    """Each heading with the text under it, down to the next heading."""
    found = list(HEADING.finditer(text))
    sections = []
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(text)
        sections.append((match.group(2), text[match.end() : end]))
    return sections


def _kind_of(heading: str) -> str | None:
    words = re.findall(r"[a-z-]+", heading.lower())
    for word in words:
        if word in KINDS:
            return KINDS[word]
    return None


def from_markdown(path: str, text: str) -> list[Seed]:
    """What one file states, as knowledge.

    An architecture decision record is one decision, whatever its headings say,
    because the file is the unit somebody wrote. Every other file is read by
    heading, and only headings that name a kind are taken.
    """
    if _is_record(path):
        return _from_record(path, text)

    seeds = []
    for heading, body in _sections(text):
        kind = _kind_of(heading)
        summary = _paragraph(body)
        if kind is None or not summary:
            continue
        seeds.append(
            Seed(
                KnowledgeObject(
                    id=f"{slug(Path(path).stem)}-{slug(heading)}",
                    kind=kind,
                    status="proposed",
                    summary=summary,
                    properties={"source": path, "heading": heading},
                ),
                path,
            )
        )
    return seeds


def _is_record(path: str) -> bool:
    parts = Path(path).parts
    return any(part.lower() in {"adr", "adrs", "decisions"} for part in parts)


def _from_record(path: str, text: str) -> list[Seed]:
    headings = _sections(text)
    title = headings[0][0] if headings else ""
    if not title:
        match = ADR_NAME.match(Path(path).name)
        title = (match.group(1) if match else Path(path).stem).replace("-", " ")

    # An ADR says what was decided under a heading of its own. Where it does
    # not, the first paragraph is the nearest thing to it.
    decided = ""
    for heading, body in headings:
        if "decision" in heading.lower() or "decided" in heading.lower():
            decided = _paragraph(body)
            break
    if not decided:
        decided = _paragraph(text)
    if not decided:
        return []

    properties = {"source": path, "title": title}
    for heading, body in headings:
        lowered = heading.lower()
        if "context" in lowered or "problem" in lowered:
            why = _paragraph(body)
            if why:
                properties["why"] = why
            break

    return [
        Seed(
            KnowledgeObject(
                id=slug(Path(path).stem),
                kind="decision",
                status="proposed",
                summary=decided,
                properties=properties,
            ),
            path,
        )
    ]


def read(root: Path, sources: tuple[str, ...] = SOURCES) -> list[Seed]:
    """Everything the repository at root states about itself.

    An object already recorded is not this function's business: it answers what
    the files say, and whoever stores it decides what to do about one that is
    already there.
    """
    root = Path(root)
    seeds: list[Seed] = []
    seen: set[str] = set()

    for source in sources:
        target = root / source
        if target.is_dir():
            files = sorted(target.rglob("*.md"))
        elif target.is_file():
            files = [target]
        else:
            continue

        for file in files:
            relative = file.relative_to(root).as_posix()
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for seed in from_markdown(relative, text):
                if seed.knowledge.id in seen:
                    continue
                seen.add(seed.knowledge.id)
                seeds.append(seed)

    return seeds
