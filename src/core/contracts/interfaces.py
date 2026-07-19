from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    ContractComparison,
    ContractDocument,
    ContractPayload,
    ContractQuery,
    ContractResult,
    ContractSnapshot,
)


@runtime_checkable
class ContractExtractor(Protocol):
    def supports(self, document: ContractDocument) -> bool: ...

    def extract(self, payload: ContractPayload) -> ContractSnapshot: ...


@runtime_checkable
class ContractComparator(Protocol):
    def compare(
        self, before: ContractSnapshot, after: ContractSnapshot
    ) -> ContractComparison: ...


@runtime_checkable
class ContractAdapter(ContractExtractor, ContractComparator, Protocol):
    pass


@runtime_checkable
class ContractReader(Protocol):
    def search(self, query: ContractQuery) -> ContractResult: ...

    def get_snapshot(
        self, scope: Scope, snapshot_id: str
    ) -> ContractSnapshot | None: ...

    def get_comparison(
        self, scope: Scope, comparison_id: str
    ) -> ContractComparison | None: ...


@runtime_checkable
class ContractWriter(Protocol):
    def put_snapshot(self, scope: Scope, snapshot: ContractSnapshot) -> None: ...

    def put_comparison(self, scope: Scope, comparison: ContractComparison) -> None: ...


@runtime_checkable
class ContractRepository(ContractReader, ContractWriter, Protocol):
    pass
