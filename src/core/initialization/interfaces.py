from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    CandidateAssessment,
    EvidenceBundle,
    EvidenceQuery,
    InitializationCandidate,
    InitializationLimits,
)


@runtime_checkable
class InitializationEvidenceReader(Protocol):
    def discover(
        self, query: EvidenceQuery, limits: InitializationLimits
    ) -> EvidenceBundle: ...

    def investigate(self, query: EvidenceQuery) -> EvidenceBundle: ...


@runtime_checkable
class InitializationCandidatePolicy(Protocol):
    def assess(self, candidate: InitializationCandidate) -> CandidateAssessment: ...
