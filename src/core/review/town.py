"""A pull request written as a Codetown town.

Codetown (github.com/sourceant/codetown) draws software as a city. This writes
its text from what a review already knows: the folders a change touches become
buildings in districts, the definitions it changes become workers marked new or
modified, callers and callees become roads, findings become the hazard their
impact names, behaviors become signposts, and tests touched become badges.
Drawing is left to the library; nothing here decides how anything looks.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from .codetown import allowed, word
from .impact import is_test

MEMBERS_PER_PLACE = 9
PURPOSE = re.compile(rf"^({'|'.join(allowed('purposes'))})(\([^)]*\))?!?:\s*(.+)$")

# Every word written here, checked against the library's vocabulary on import.
SYSTEM, INTENT, PURPOSE_WORD, DISTRICT = (
    word(one) for one in ("system", "intent", "purpose", "district")
)
SERVICE, FUNCTION, CALLS = word("service"), word("function"), word("calls")
BEHAVIOR, RISK, TEST = word("behavior"), word("risk"), word("test")
IMPACTS, CERTAINTIES, BLASTS, TRIGGERS = (
    set(allowed(one)) for one in ("impacts", "certainties", "blasts", "triggers")
)
SKIPPED = re.compile(r"(^|/)(fixtures?|__snapshots__|vendor|node_modules|dist)/")


def _quote(text: str) -> str:
    return '"' + " ".join(str(text).split()).replace('"', '\\"') + '"'


def _ident(text: str) -> str:
    """An id Codetown accepts: letters, digits and underscores."""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    return cleaned or "root"


def _folder(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


def _district(folder: str) -> str:
    """The area a folder belongs to: its first two segments, without `src`."""
    parts = [one for one in folder.split("/") if one and one != "src"]
    return "/".join(parts[:1]) or "root"


def _first_sentence(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s", " ".join(str(text).split()), maxsplit=1)[0]
    return sentence[:120]


class _Places:
    def __init__(self) -> None:
        self.by_folder: dict[str, str] = {}
        self.ids: set[str] = set()

    def id_for(self, folder: str) -> str:
        if folder not in self.by_folder:
            parts = [one for one in folder.split("/") if one and one != "src"]
            base = _ident("_".join(parts[-2:]) if parts else "root")
            candidate, n = base, 2
            while candidate in self.ids:
                candidate, n = f"{base}_{n}", n + 1
            self.by_folder[folder] = candidate
            self.ids.add(candidate)
        return self.by_folder[folder]


def town(
    impact: dict[str, Any],
    review: dict[str, Any] | None = None,
    *,
    title: str = "",
    name: str = "",
) -> str:
    """The Codetown text for one pull request."""
    review = review or {}
    summary = review.get("summary") or {}
    files = [one for one in impact.get("files", []) if not one.get("binary")]
    code = [one for one in files if not one["test"] and not SKIPPED.search(one["path"])]
    added_files = {one["path"] for one in files if one.get("added")}
    places = _Places()

    lines: list[str] = [f"{SYSTEM} {_quote(name or 'Change')}"]
    intent = (summary.get("intent") or "").strip()
    match = PURPOSE.match(title.strip())
    if intent or title:
        lines.append(
            f"{INTENT} {_quote(intent or (match.group(3) if match else title))}"
        )
    if match:
        lines.append(f"{PURPOSE_WORD} {match.group(1)} {_quote(match.group(3))}")

    # Places: each folder holding changed code, in the district it belongs to.
    folders: dict[str, list[dict[str, Any]]] = {}
    for file in code:
        folders.setdefault(_folder(file["path"]), []).append(file)
    definitions = [
        one
        for one in impact.get("definitions", [])
        if one["path"] in {f["path"] for f in code}
    ]
    consumers = [one for one in impact.get("consumers", []) if not one.get("test")]
    for consumer in consumers:
        folders.setdefault(_folder(consumer["path"]), [])

    districts = sorted({_district(folder) for folder in folders})
    if len(districts) > 1:
        lines.append("")
        for district in districts:
            lines.append(f"{DISTRICT} {_ident(district)} {_quote(district)}")

    member_of: dict[tuple[str, int], tuple[str, str]] = {}
    for folder in sorted(folders):
        changed = folders[folder]
        place = places.id_for(folder)
        new = bool(changed) and all(one["path"] in added_files for one in changed)
        size = max(
            1,
            min(
                5,
                1 + sum(one["additions"] + one["deletions"] for one in changed) // 150,
            ),
        )
        options = [f"in={_ident(_district(folder))}"] if len(districts) > 1 else []
        if size != 2:
            options.append(f"size={size}")
        mark = "+ " if new else ""
        lines.append("")
        lines.append(
            f"{mark}{SERVICE} {place} {_quote(folder or '/')}{' ' + ' '.join(options) if options else ''}"
        )
        seen: set[str] = set()
        for definition in [
            one for one in definitions if _folder(one["path"]) == folder
        ][:MEMBERS_PER_PLACE]:
            member = (
                definition["name"]
                if re.fullmatch(r"[A-Za-z0-9_$]+", definition["name"])
                else _ident(definition["name"])
            )
            if member in seen:
                continue
            seen.add(member)
            member_of[(definition["path"], definition["start"])] = (place, member)
            changed_mark = "+" if definition["path"] in added_files else "~"
            lines.append(f"  {changed_mark} {FUNCTION} {member}")

    # Roads: a changed definition's callers and callees in other folders.
    roads: set[tuple[str, str]] = set()
    for definition in definitions:
        here = places.id_for(_folder(definition["path"]))
        for caller in definition.get("callers", []):
            if is_test(caller["path"]) or SKIPPED.search(caller["path"]):
                continue
            there = places.id_for(_folder(caller["path"]))
            if there != here and _folder(caller["path"]) in folders:
                roads.add((there, here))
        for callee in definition.get("callees", []):
            there = places.id_for(_folder(callee["path"]))
            if there != here and _folder(callee["path"]) in folders:
                roads.add((here, there))
    if roads:
        lines.append("")
        lines.extend(f"{a} {CALLS} {b}" for a, b in sorted(roads))

    def target(path: str, line: int | None = None) -> str | None:
        folder = _folder(path)
        if folder not in folders:
            return None
        place = places.id_for(folder)
        if line is not None:
            for definition in definitions:
                if (
                    definition["path"] == path
                    and definition["start"] <= line <= definition["end"]
                ):
                    hit = member_of.get((path, definition["start"]))
                    if hit:
                        return f"{hit[0]}.{hit[1]}"
        return place

    behaviors = []
    for n, behavior in enumerate(summary.get("behaviors") or [], start=1):
        where = next((one for one in behavior.get("locations", []) if ":" in one), "")
        path, _, number = where.rpartition(":")
        at = target(path, int(number) if number.isdigit() else None)
        if at:
            behaviors.append(
                f"{BEHAVIOR} B{n} at {at} {_quote(behavior.get('before') or 'Did not exist')} -> {_quote(behavior.get('after') or 'No longer happens')}"
            )
    if behaviors:
        lines.append("")
        lines.extend(behaviors)

    risks = []
    for suggestion in review.get("code_suggestions") or []:
        if suggestion.get("severity") == "nit":
            continue
        at = target(suggestion.get("file_name", ""), suggestion.get("start_line"))
        if not at:
            continue
        # A value the library does not know would fail the whole town, so it falls back.
        impact = suggestion.get("impact")
        certainty = suggestion.get("certainty")
        blast = suggestion.get("blast")
        options = [
            f"impact={impact if impact in IMPACTS else 'none'}",
            f"certainty={certainty if certainty in CERTAINTIES else 'possible'}",
            f"blast={blast if blast in BLASTS else 'one'}",
        ]
        if suggestion.get("trigger") in TRIGGERS:
            options.append(f"trigger={suggestion['trigger']}")
        risks.append(
            f"{RISK} {len(risks) + 1} at {at} {_quote(_first_sentence(suggestion.get('comment', '')))} {' '.join(options)}"
        )
    if risks:
        lines.append("")
        lines.extend(risks)

    tests: dict[str, list[str]] = {}
    for file in files:
        if not file["test"] or SKIPPED.search(file["path"]):
            continue
        stem = re.sub(
            r"^test_|[._-](test|spec)s?$", "", PurePosixPath(file["path"]).stem
        )
        home = next(
            (
                one["path"]
                for one in code
                if PurePosixPath(one["path"]).stem in (stem, stem.split("_")[-1])
            ),
            None,
        )
        if home:
            tests.setdefault(places.id_for(_folder(home)), []).append(
                PurePosixPath(file["path"]).name
            )
    if tests:
        lines.append("")
        for place, names in sorted(tests.items()):
            count = f" count={len(names)}" if len(names) > 1 else ""
            lines.append(
                f"{TEST} {place} {_quote(names[0] if len(names) == 1 else ', '.join(names[:3]))}{count}"
            )

    return "\n".join(lines) + "\n"
