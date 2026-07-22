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
class CompatibilityEvidenceReader(Protocol):
    def read(
        self, query: CompatibilityEvidenceQuery
    ) -> tuple[CompatibilityEvidence, ...]: ...


@runtime_checkable
class ReviewImpactPreparer(Protocol):
    def prepare(self, request: ReviewImpactRequest) -> ReviewImpact: ...
