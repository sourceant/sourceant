from __future__ import annotations

from .interfaces import KnowledgeSelector
from .models import KnowledgeObject, KnowledgeSelection


class LinkedKnowledgeSelector:
    def __init__(self, knowledge: KnowledgeSelector) -> None:
        if not isinstance(knowledge, KnowledgeSelector):
            raise TypeError("Knowledge selection requires a KnowledgeSelector backend")
        self._knowledge = knowledge

    def select(self, selection: KnowledgeSelection) -> tuple[KnowledgeObject, ...]:
        return self._knowledge.select(selection)
