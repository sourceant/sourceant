from __future__ import annotations

from collections.abc import Sequence

from src.core.scope import Scope

from .interfaces import ContractAdapter, ContractRepository
from .models import ContractPayload, ContractProcessingResult


class UnsupportedContractFormatError(ValueError):
    pass


class AmbiguousContractAdapterError(ValueError):
    pass


class DefaultContractProcessor:
    def __init__(
        self,
        adapters: Sequence[ContractAdapter],
        repository: ContractRepository,
    ) -> None:
        self._adapters = tuple(adapters)
        self._repository = repository

    def process(
        self,
        scope: Scope,
        payload: ContractPayload,
        baseline_snapshot_id: str | None = None,
    ) -> ContractProcessingResult:
        adapters = tuple(
            adapter for adapter in self._adapters if adapter.supports(payload.document)
        )
        if not adapters:
            raise UnsupportedContractFormatError(
                f"no contract adapter supports {payload.document.format}"
            )
        if len(adapters) > 1:
            raise AmbiguousContractAdapterError(
                f"multiple contract adapters support {payload.document.format}"
            )

        adapter = adapters[0]
        snapshot = adapter.extract(payload)
        if baseline_snapshot_id == snapshot.id:
            self._repository.put_snapshot(scope, snapshot)
            return ContractProcessingResult(snapshot)

        comparison = None
        if baseline_snapshot_id is not None:
            baseline = self._repository.get_snapshot(scope, baseline_snapshot_id)
            if baseline is None:
                raise ValueError("baseline snapshot does not exist in scope")
            comparison = adapter.compare(baseline, snapshot)

        self._repository.put_snapshot(scope, snapshot)
        if comparison is not None:
            self._repository.put_comparison(scope, comparison)
        return ContractProcessingResult(snapshot, comparison)
