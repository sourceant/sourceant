"""Working out which file an import names.

The parser reports an import as the text that was written: `./charge`,
`src.core.scope`, `github.com/acme/billing/ledger`. Left at that, a repository
draws as one island per file, because nothing joins a file to the file it uses.
The connections between files are most of what a person is looking for.

Resolution is by matching against the paths the repository actually has, rather
than by implementing each language's module system. A repository is a closed set
of files, so the question "which of these did they mean" is answerable without
knowing how the language would answer it, and a rule per language would be one
more thing to be wrong per language.

Nothing is guessed. Where two files match equally well the import is left
unresolved, because a line drawn to the wrong file is worse than no line: it is
read as fact.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# What a module might be written as, once the extension is dropped.
INDEXES = ("index", "__init__", "mod", "lib")


def _without_extension(path: str) -> str:
    head, _, tail = path.rpartition("/")
    stem, dot, _ = tail.rpartition(".")
    if not dot:
        return path
    return f"{head}/{stem}" if head else stem


def _candidates(path: str) -> tuple[str, ...]:
    """The names an import could plausibly use for this file.

    A file is reachable as its own path, as that path without the extension,
    and, when it is a directory's entry point, as the directory itself.
    """
    bare = _without_extension(path)
    head, _, tail = bare.rpartition("/")
    if tail in INDEXES and head:
        return (path, bare, head)
    return (path, bare)


def index_paths(paths: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """Every name a file could be imported as, pointing back at the file.

    A name several files answer to is kept with all of them, so the caller can
    tell an ambiguous import from a resolved one.
    """
    by_name: dict[str, list[str]] = {}
    for path in paths:
        for candidate in _candidates(path):
            by_name.setdefault(candidate, []).append(path)
    return {name: tuple(sorted(found)) for name, found in by_name.items()}


def index_directories(paths: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """What sits directly in each directory.

    Some languages import a directory rather than a file: a Go import names a
    package, and the package is every file in that folder. Without this those
    imports resolve to nothing, because no single file answers to the name.
    """
    inside: dict[str, list[str]] = {}
    for path in paths:
        head, sep, _ = path.rpartition("/")
        if sep:
            inside.setdefault(head, []).append(path)
    return {name: tuple(sorted(found)) for name, found in inside.items()}


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

    Usually one file. A language that imports a directory rather than a file
    names all of them at once, and answering with the whole package is truer
    than answering with whichever file happens to sort first.

    Relative imports are resolved against the importing file and go no further:
    `./charge` in one directory is not `charge` in another, and treating it as
    such would join two files that have nothing to do with each other.
    """
    if not source:
        return ()
    inside = inside or {}

    if source.startswith("."):
        target = _relative(importer, source)
        if target is None:
            return ()
        return _at(by_name, inside, target, importer)

    # A dotted name is a path in every language that writes them that way.
    dotted = source.replace(".", "/") if "/" not in source else source
    for candidate in (source, dotted):
        found = _at(by_name, inside, candidate, importer)
        if found:
            return found

    return _by_tail(by_name, inside, importer, dotted)


def _at(
    by_name: Mapping[str, tuple[str, ...]],
    inside: Mapping[str, tuple[str, ...]],
    name: str,
    importer: str,
) -> tuple[str, ...]:
    """What one name points at: a file, or the package of that name."""
    found = by_name.get(name)
    if found and len(found) == 1:
        return () if found[0] == importer else found
    package = inside.get(name)
    if package:
        kept = tuple(path for path in package if path != importer)
        return kept
    return ()


def _by_tail(
    by_name: Mapping[str, tuple[str, ...]],
    inside: Mapping[str, tuple[str, ...]],
    importer: str,
    source: str,
) -> tuple[str, ...]:
    """A package-qualified import, matched on the end of the path.

    `github.com/acme/billing/ledger` ends in the part that is a path inside the
    repository. The longest end that matches wins, so a bare `fmt` never does.
    """
    segments = [segment for segment in source.split("/") if segment]
    for start in range(len(segments) - 1):
        found = _at(by_name, inside, "/".join(segments[start:]), importer)
        if found:
            return found
    return ()
