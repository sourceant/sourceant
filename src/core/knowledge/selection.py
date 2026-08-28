from __future__ import annotations

from .interfaces import KnowledgeLinkReader, KnowledgeReader
from .models import KnowledgeObject, KnowledgeQuery, KnowledgeSelection


class LinkedKnowledgeSelector:
    """Knowledge linked to the files a change touches.

    Exact and shallow: a decision nobody attached to those files is not
    returned, however clearly it governs them.
    """

    def __init__(self, knowledge: KnowledgeReader) -> None:
        self._knowledge = knowledge

    def select(self, selection: KnowledgeSelection) -> tuple[KnowledgeObject, ...]:
        if not selection.paths or not isinstance(self._knowledge, KnowledgeLinkReader):
            return ()
        identities = self._knowledge.knowledge_ids_for_paths(
            selection.scope, frozenset(selection.paths)
        )
        if not identities:
            return ()
        found = self._knowledge.search(
            KnowledgeQuery(
                scope=selection.scope,
                ids=frozenset(identities),
                limit=selection.limit,
            )
        )
        return found.items
