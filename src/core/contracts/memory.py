from __future__ import annotations

from src.core.scope import Scope

from .models import (
    ContractComparison,
    ContractQuery,
    ContractResult,
    ContractSnapshot,
)


class InMemoryContractRepository:
    def __init__(self) -> None:
        self._snapshots: dict[tuple[Scope, str], ContractSnapshot] = {}
        self._comparisons: dict[tuple[Scope, str], ContractComparison] = {}

    def put_snapshot(self, scope: Scope, snapshot: ContractSnapshot) -> None:
        self._snapshots[(scope, snapshot.id)] = snapshot

    def put_comparison(self, scope: Scope, comparison: ContractComparison) -> None:
        if (scope, comparison.before_snapshot_id) not in self._snapshots:
            raise ValueError("comparison before snapshot does not exist in scope")
        if (scope, comparison.after_snapshot_id) not in self._snapshots:
            raise ValueError("comparison after snapshot does not exist in scope")
        self._comparisons[(scope, comparison.id)] = comparison

    def search(self, query: ContractQuery) -> ContractResult:
        matches = sorted(
            (
                snapshot
                for (scope, _), snapshot in self._snapshots.items()
                if scope == query.scope
                and (
                    not query.document_ids or snapshot.document.id in query.document_ids
                )
                and (not query.formats or snapshot.document.format in query.formats)
            ),
            key=lambda snapshot: snapshot.id,
        )
        items = tuple(matches[query.offset : query.offset + query.limit])
        return ContractResult(
            items=items,
            total=len(matches),
            has_more=query.offset + len(items) < len(matches),
        )

    def get_snapshot(self, scope: Scope, snapshot_id: str) -> ContractSnapshot | None:
        return self._snapshots.get((scope, snapshot_id))

    def get_comparison(
        self, scope: Scope, comparison_id: str
    ) -> ContractComparison | None:
        return self._comparisons.get((scope, comparison_id))
