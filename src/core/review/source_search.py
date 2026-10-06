from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

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
        for path in sorted(self.paths):
            content = self.read_content(path)
            if content is None:
                continue
            lines = content.splitlines()
            last_end = 0
            for number, line in enumerate(lines, start=1):
                score = sum(term in line.casefold() for term in wanted)
                if not score or number <= last_end:
                    continue
                start, end = max(1, number - 3), min(len(lines), number + 3)
                last_end = end
                count += 1
                matches.append(
                    (
                        score,
                        SearchMatch(
                            path=path,
                            revision=self.revision
                            or str(self.scope.get("revision") or ""),
                            start_line=start,
                            end_line=end,
                            text="\n".join(lines[start - 1 : end]),
                            repository=str(self.scope.get("repository")),
                        ),
                    )
                )
                matches.sort(
                    key=lambda item: (-item[0], item[1].path, item[1].start_line)
                )
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
