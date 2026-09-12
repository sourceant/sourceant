from __future__ import annotations

from src.core.scope import Scope
from src.core.topology import TopologyQuery, TopologyReader

from .interfaces import ImpactSeedResolver
from .models import ChangedCodeReference

#: The graph refuses a page larger than this.
PAGE = 100

#: Enough pages to cover a repository read into parts, and a stop so a graph
#: somebody filled with a script cannot hold a review open.
MAX_PAGES = 20


class TopologyPrefixSeedResolver:
    """Where a changed file starts its walk, worked out rather than looked up.

    A stored mapping is written while a repository is read. Nothing has read a
    repository connected an hour ago, so a review of it starts nowhere and
    reaches nothing, however much the graph holds. The graph already says
    which part of a system holds which folder, and that is the same answer.

    Longest prefix wins: `src/core` holds `src/core/jobs/sql.py` unless there
    is a `src/core/jobs` of its own. The system itself answers when no part
    claims the path, because a change in a repository reaches whatever that
    repository reaches, whichever folder it happens to sit in.
    """

    def __init__(self, topology: TopologyReader) -> None:
        self._topology = topology

    def resolve(
        self, scope: Scope, changes: tuple[ChangedCodeReference, ...]
    ) -> tuple[str, ...]:
        found: set[str] = set()
        for repository in sorted(
            {change.repository for change in changes if change.repository}
        ):
            system = self._system(scope, repository)
            if system is None:
                continue
            parts = self._parts(scope, system)
            for change in changes:
                if change.repository != repository:
                    continue
                path = change.path or change.id
                held = next(
                    (entity for prefix, entity in parts if path.startswith(prefix)),
                    system,
                )
                found.add(held)
        return tuple(sorted(found))

    def _system(self, scope: Scope, repository: str) -> str | None:
        """The entity a repository is, which is what a reading of it wrote."""
        result = self._topology.search(
            TopologyQuery(
                scope,
                kinds=frozenset({"system"}),
                properties={"name": repository},
                limit=2,
            )
        )
        if len(result.entities) != 1:
            return None
        return result.entities[0].id

    def _parts(self, scope: Scope, system: str) -> tuple[tuple[str, str], ...]:
        """The parts of one system, longest folder first."""
        parts: list[tuple[str, str]] = []
        for page in range(MAX_PAGES):
            result = self._topology.search(
                TopologyQuery(
                    scope,
                    kinds=frozenset({"component"}),
                    properties={"system_id": system},
                    limit=PAGE,
                    offset=page * PAGE,
                )
            )
            parts.extend(
                (str(entity.properties.get("external_id") or ""), entity.id)
                for entity in result.entities
                if entity.properties.get("external_id")
            )
            if not result.has_more:
                break
        return tuple(sorted(parts, key=lambda pair: len(pair[0]), reverse=True))


class FallbackSeedResolver:
    """Where a walk starts: the stored mapping, or the graph's own shape.

    Asked in that order because a mapping was written by something that read
    the repository and knows more than a folder name does.
    """

    def __init__(
        self,
        primary: ImpactSeedResolver,
        fallback: ImpactSeedResolver,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def resolve(
        self, scope: Scope, changes: tuple[ChangedCodeReference, ...]
    ) -> tuple[str, ...]:
        return self._primary.resolve(scope, changes) or self._fallback.resolve(
            scope, changes
        )
