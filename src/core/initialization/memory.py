from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from .models import (
    EvidenceBundle,
    EvidenceQuery,
    InitializationEvidence,
    InitializationLimits,
)


class InMemoryInitializationEvidenceReader:
    def __init__(self, items: Iterable[InitializationEvidence] = ()):
        self._items = tuple(items)

    def discover(
        self, query: EvidenceQuery, limits: InitializationLimits
    ) -> EvidenceBundle:
        return self._select(query, min(query.limit, limits.evidence_limit))

    def investigate(self, query: EvidenceQuery) -> EvidenceBundle:
        return self._select(query, query.limit)

    def _select(self, query: EvidenceQuery, limit: int) -> EvidenceBundle:
        matched = [
            item
            for item in self._items
            if item.scope == query.scope
            and (not query.kinds or item.kind in query.kinds)
            and (not query.identifiers or item.id in query.identifiers)
            and (
                not query.intents
                or any(
                    intent.lower() in f"{item.summary}\n{item.content}".lower()
                    for intent in query.intents
                )
            )
        ]
        selected = []
        characters = 0
        truncated = len(matched) > limit
        for item in matched[:limit]:
            size = len(item.summary) + len(item.content)
            remaining = query.character_limit - characters
            if size > remaining:
                truncated = True
                available_content = remaining - len(item.summary)
                if available_content > 0:
                    selected.append(
                        replace(item, content=item.content[:available_content])
                    )
                break
            selected.append(item)
            characters += size
        return EvidenceBundle(query.scope, tuple(selected), truncated)
