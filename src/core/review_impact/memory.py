from __future__ import annotations

from src.core.scope import Scope

from .models import (
    ChangedCodeReference,
    CompatibilityEvidence,
    CompatibilityEvidenceQuery,
)


class InMemoryImpactSeedResolver:
    def __init__(self) -> None:
        self._mappings: dict[tuple[Scope, str], tuple[str, ...]] = {}

    def put(self, scope: Scope, code_id: str, entity_ids: tuple[str, ...]) -> None:
        if not code_id or not entity_ids or any(not item for item in entity_ids):
            raise ValueError("code and topology identities are required")
        self._mappings[(scope, code_id)] = tuple(sorted(set(entity_ids)))

    def resolve(
        self, scope: Scope, changes: tuple[ChangedCodeReference, ...]
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    entity_id
                    for change in changes
                    for entity_id in self._mappings.get((scope, change.id), ())
                }
            )
        )


class InMemoryCompatibilityEvidenceReader:
    def __init__(self) -> None:
        self._items: dict[tuple[Scope, str], CompatibilityEvidence] = {}

    def put(self, scope: Scope, evidence: CompatibilityEvidence) -> None:
        self._items[(scope, evidence.id)] = evidence

    def read(
        self, query: CompatibilityEvidenceQuery
    ) -> tuple[CompatibilityEvidence, ...]:
        matches = sorted(
            (
                item
                for (item_scope, _), item in self._items.items()
                if item_scope == query.scope
                and item.provider_entity_id in query.entity_ids
                and item.consumer_entity_id in query.entity_ids
                and (not query.statuses or item.status in query.statuses)
                and item.confidence >= query.minimum_confidence
                and (query.include_stale or not item.stale)
            ),
            key=lambda item: item.id,
        )
        return tuple(matches[: query.limit])
