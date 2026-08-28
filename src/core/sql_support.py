from __future__ import annotations

from typing import Any, Callable, Iterable, Iterator, Sequence

# SQLite compiled before 3.32 binds at most 999 parameters in one statement, and
# a query built from a repository's symbols or a pull request's files is only as
# small as the repository or the pull request. Every collection that a caller
# sizes goes through here.
CHUNK = 400


def chunked(values: Iterable[str], size: int = CHUNK) -> Iterator[list[str]]:
    ordered = sorted({value for value in values if value})
    if not ordered:
        return
    for start in range(0, len(ordered), size):
        yield ordered[start : start + size]


def rows_for(
    values: Iterable[str],
    query: Callable[[Sequence[str]], Iterable[Any]],
    size: int = CHUNK,
) -> list[Any]:
    """Run one query per chunk of values and return every row they found."""
    found: list[Any] = []
    for chunk in chunked(values, size):
        found.extend(query(chunk))
    return found
