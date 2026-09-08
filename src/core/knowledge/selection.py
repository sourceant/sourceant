from __future__ import annotations

from fnmatch import fnmatchcase

from .characteristics import KnowledgeApplicability
from .interfaces import KnowledgeLinkReader, KnowledgeReader
from .models import KnowledgeObject, KnowledgeQuery, KnowledgeSelection


class LinkedKnowledgeSelector:
    def __init__(self, knowledge: KnowledgeReader) -> None:
        self._knowledge = knowledge

    def select(self, selection: KnowledgeSelection) -> tuple[KnowledgeObject, ...]:
        identities = frozenset()
        if selection.paths and isinstance(self._knowledge, KnowledgeLinkReader):
            identities = self._knowledge.knowledge_ids_for_paths(
                selection.scope, frozenset(selection.paths)
            )
        selected = []
        offset = 0
        while True:
            page = self._knowledge.search(
                KnowledgeQuery(
                    scope=selection.scope,
                    statuses=frozenset({"active", "accepted", "approved"}),
                    limit=100,
                    offset=offset,
                )
            )
            for item in page.items:
                applicable = item.applicability == KnowledgeApplicability.SCOPE
                linked = item.id in identities
                paths = item.properties.get("paths", ())
                matched = any(
                    path == pattern
                    or path.startswith(pattern.rstrip("/") + "/")
                    or fnmatchcase(path, pattern)
                    for path in selection.paths
                    for pattern in paths
                )
                if applicable or linked or matched:
                    selected.append(item)
            if not page.has_more or not page.items:
                break
            offset += len(page.items)
        selected.sort(key=lambda item: (-item.importance.priority, item.id))
        return tuple(selected[: selection.limit])
