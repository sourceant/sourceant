from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.scope import Scope

from .models import (
    ChangedCodeReference,
    CompatibilityEvidence,
    CompatibilityEvidenceQuery,
    ReviewImpact,
    ReviewImpactRequest,
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
class CompatibilityEvidenceReader(Protocol):
    """Read deterministically ordered evidence after filtering, up to the limit."""

    def read(
        self, query: CompatibilityEvidenceQuery
    ) -> tuple[CompatibilityEvidence, ...]: ...


@runtime_checkable
class CompatibilityEvidenceWriter(Protocol):
    def put_evidence(
        self, scope: Scope, evidence: CompatibilityEvidence
    ) -> None: ...


@runtime_checkable
class CompatibilityEvidenceRepository(
    CompatibilityEvidenceReader, CompatibilityEvidenceWriter, Protocol
):
    pass


@runtime_checkable
class ReviewImpactPreparer(Protocol):
    def prepare(self, request: ReviewImpactRequest) -> ReviewImpact: ...
