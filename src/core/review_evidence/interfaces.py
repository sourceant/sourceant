from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import EvidenceDecision, FileEvidence, ReviewClaim


@runtime_checkable
class ChangedFileEvidenceReader(Protocol):
    def read(self, path: str) -> FileEvidence | None: ...


@runtime_checkable
class ReviewEvidenceValidator(Protocol):
    def validate(
        self, claims: list[ReviewClaim], evidence: FileEvidence | None
    ) -> EvidenceDecision: ...
