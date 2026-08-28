from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    ChangedCodeReference,
    CompatibilityCheck,
    CompatibilityCheckQuery,
    ChangeImpact,
    ChangeImpactRequest,
)


@runtime_checkable
class ImpactSeedResolver(Protocol):
    def resolve(
        self, scope: Scope, changes: tuple[ChangedCodeReference, ...]
    ) -> tuple[str, ...]: ...


@runtime_checkable
class ImpactCodeMappingWriter(Protocol):
    def put_mapping(
        self,
        scope: Scope,
        change: ChangedCodeReference,
        entity_ids: tuple[str, ...],
    ) -> None: ...


@runtime_checkable
class ImpactSeedRepository(ImpactSeedResolver, ImpactCodeMappingWriter, Protocol):
    pass


@runtime_checkable
class CompatibilityCheckReader(Protocol):
    """Read deterministically ordered evidence after filtering, up to the limit."""

    def read(
        self, query: CompatibilityCheckQuery
    ) -> tuple[CompatibilityCheck, ...]: ...


@runtime_checkable
class CompatibilityCheckWriter(Protocol):
    def put_evidence(self, scope: Scope, evidence: CompatibilityCheck) -> None: ...


@runtime_checkable
class CompatibilityCheckRepository(
    CompatibilityCheckReader, CompatibilityCheckWriter, Protocol
):
    pass


@runtime_checkable
class ChangeImpactResolver(Protocol):
    def resolve(self, request: ChangeImpactRequest) -> ChangeImpact: ...
