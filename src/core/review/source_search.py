from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence
import subprocess

from src.core.search.source_text import SourceTextSearch
from src.core.scope import Scope
from src.core.search import Searcher, SearchQuery, SearchMatch, SearchResult
from src.core.services import ServiceRegistry


@dataclass
class SnapshotSearcher:
    scope: Scope
    paths: Sequence[str]
    read_content: Callable[[str], str | None]
    fallback: Searcher | None = None
    revision: str = ""
    _text: SourceTextSearch = field(init=False, repr=False)

    def __post_init__(self):
        self._text = SourceTextSearch(self.paths, self.read_content)

    def search(self, query: SearchQuery) -> SearchResult:
        if query.scope.get("repository") != self.scope.get(
            "repository"
        ) or query.scope.get("revision") not in {
            self.scope.get("revision"),
            self.revision or self.scope.get("revision"),
        }:
            return (
                self.fallback.search(query)
                if self.fallback is not None
                else SearchResult(
                    unavailable="Requested repository revision is unavailable"
                )
            )
        wanted = tuple(term.casefold() for term in query.terms)
        matches = []
        count = 0
        last_ends = {}
        try:
            found = self._text.matching_lines(query.terms)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            return SearchResult(unavailable=str(error))
        for path, number, line in found:
            lines = self._text.source_lines(path)
            score = sum(term in line.casefold() for term in wanted)
            if not score or number <= last_ends.get(path, 0):
                continue
            start, end = max(1, number - 3), min(len(lines), number + 3)
            last_ends[path] = end
            count += 1
            matches.append(
                (
                    score,
                    SearchMatch(
                        path=path,
                        revision=self.revision or str(self.scope.get("revision") or ""),
                        start_line=start,
                        end_line=end,
                        text="\n".join(lines[start - 1 : end]),
                        repository=str(self.scope.get("repository")),
                    ),
                )
            )
            matches.sort(key=lambda item: (-item[0], item[1].path, item[1].start_line))
            del matches[query.limit :]

        return SearchResult(
            matches=tuple(item[1] for item in matches[: query.limit]),
            truncated=count > query.limit,
        )


def searchable_snapshot(
    services: ServiceRegistry, scope: Scope, paths, read_content, *, revision=""
):
    try:
        fallback = services.resolve(Searcher)
    except LookupError:
        fallback = None
    return services.with_service(
        Searcher,
        SnapshotSearcher(scope, tuple(paths), read_content, fallback, revision),
        "snapshot",
    )
