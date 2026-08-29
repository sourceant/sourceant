"""Working out which file an import names, when nothing authoritative has.

This is the last of three ways to answer that, and the only one that needs
nothing installed.

The accurate way is to ask the compiler: an indexer that runs the real
toolchain knows exactly what a name binds to, and `scip.py` reads the result.
The rigorous way without a build is to write name binding rules per language
and resolve by walking the graph they describe, which is what stack graphs do.
Both are per-language work.

This is neither. It matches the text of an import against the paths the
repository has, and is right often enough to be worth having and wrong often
enough that it must never be mistaken for the other two. Everything it produces
is marked inferred, and where two files fit equally well it produces nothing:
a line to the wrong file is worse than no line, because a drawing is read as
fact.

The matching itself knows no languages. An import is a run of names with
punctuation between them, and a path is a run of names with slashes between
them; whichever punctuation a language chose, the tail is the same. So both
sides are cut into names and the longest run that ends the same way wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

# Every character a language has used to separate one name from the next. This
# is punctuation, not a list of languages: nothing here needs to know which of
# them writes a namespace and which writes a path.
SEPARATORS = re.compile(r"[/\\.:]+")


def names(text: str) -> tuple[str, ...]:
    """The names in something, whatever was written between them."""
    return tuple(part for part in SEPARATORS.split(text.strip()) if part)


def _tails(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Every run of names that ends where this one ends, longest first.

    An import almost never names a file from the root of the repository: it
    names it from wherever that language or that build was told the root is.
    What survives is the end, so the end is what is matched.
    """
    return tuple("/".join(parts[start:]) for start in range(len(parts)))


def _key(parts: Iterable[str]) -> str:
    # Folded, because a name and a directory routinely differ only in case.
    return "/".join(part.lower() for part in parts)


def index_paths(paths: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """Every ending a file could be named by, pointing back at the file.

    An ending several files answer to is kept with all of them, so the caller
    can tell an ambiguous import from a resolved one.
    """
    found: dict[str, set[str]] = {}
    for path in paths:
        parts = names(path)
        # With the extension and without it: an import usually omits it, and
        # occasionally does not.
        for ending in set(_tails(parts)) | set(_tails(parts[:-1])):
            if ending:
                found.setdefault(_key(ending.split("/")), set()).add(path)
    return {ending: tuple(sorted(matches)) for ending, matches in found.items()}


def index_directories(paths: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """What sits directly in each directory, by every ending that names it.

    Some imports name a container rather than a file, and the container is
    every file in it.
    """
    inside: dict[str, set[str]] = {}
    for path in paths:
        head, sep, _ = path.rpartition("/")
        if not sep:
            continue
        for ending in _tails(names(head)):
            if ending:
                inside.setdefault(_key(ending.split("/")), set()).add(path)
    return {ending: tuple(sorted(matches)) for ending, matches in inside.items()}


def _normalise(segments: list[str]) -> str | None:
    """Apply `.` and `..` to a path, or refuse if it climbs past the root."""
    out: list[str] = []
    for segment in segments:
        if segment in ("", "."):
            continue
        if segment == "..":
            if not out:
                return None
            out.pop()
            continue
        out.append(segment)
    return "/".join(out)


def _relative(importer: str, source: str) -> str | None:
    here, _, _ = importer.rpartition("/")
    return _normalise((here.split("/") if here else []) + source.split("/"))


def resolve(
    by_name: Mapping[str, tuple[str, ...]],
    importer: str,
    source: str,
    inside: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    """The files an import names, empty where the repository does not say.

    Usually one file. An import that names a container names all of them at
    once, and answering with the whole thing is truer than answering with
    whichever file happens to sort first.

    A relative import is positional rather than a name, so it is resolved
    against the importing file and goes no further: `./charge` in one directory
    is not `charge` in another, and treating it as such would join two files
    that have nothing to do with each other.
    """
    if not source.strip():
        return ()
    inside = inside or {}

    if source.lstrip().startswith("."):
        target = _relative(importer, source.strip().replace("\\", "/"))
        if target is None:
            return ()
        return _at(by_name, inside, names(target), importer)

    # Longest ending first: the more of the import that matched, the likelier
    # it is the file that was meant. A single trailing name matches too much to
    # be worth anything, so it is not tried.
    parts = names(source)
    for start in range(len(parts) - 1):
        found = _at(by_name, inside, parts[start:], importer)
        if found:
            return found
    return ()


def _at(
    by_name: Mapping[str, tuple[str, ...]],
    inside: Mapping[str, tuple[str, ...]],
    parts: tuple[str, ...],
    importer: str,
) -> tuple[str, ...]:
    """What one ending points at: a file, or everything in a container of that name."""
    if not parts:
        return ()
    key = _key(parts)

    found = by_name.get(key)
    if found and len(found) == 1:
        return () if found[0] == importer else found

    container = inside.get(key)
    if container:
        return tuple(path for path in container if path != importer)
    return ()
