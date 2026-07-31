from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from src.core.scope import Scope


@dataclass(frozen=True)
class EvidenceReference:
    source: str
    identifier: str
    revision: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        if not _present(self.source) or not _present(self.identifier):
            raise ValueError("evidence source and identifier must not be empty")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("evidence line range must be complete")
        if self.start_line is not None and (
            self.start_line < 1 or self.end_line < self.start_line
        ):
            raise ValueError("evidence line range is invalid")


@dataclass(frozen=True)
class InitializationEvidence:
    scope: Scope
    id: str
    kind: str
    summary: str
    content: str = ""
    references: tuple[EvidenceReference, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not _present(self.id)
            or not _present(self.kind)
            or not _present(self.summary)
        ):
            raise ValueError("evidence id, kind, and summary must not be empty")
        object.__setattr__(self, "properties", _immutable_mapping(self.properties))

    def __hash__(self) -> int:
        return hash(
            (
                self.scope,
                self.id,
                self.kind,
                self.summary,
                self.content,
                self.references,
                _hashable(self.properties),
            )
        )


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _immutable_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_immutable(item) for item in value)
    return value


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _immutable(item) for key, item in value.items()})


def _hashable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((key, _hashable(item)) for key, item in value.items()))
    if isinstance(value, tuple):
        return tuple(_hashable(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_hashable(item) for item in value)
    return value


def _present(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class EvidenceQuery:
    scope: Scope
    intents: tuple[str, ...] = ()
    kinds: frozenset[str] = field(default_factory=frozenset)
    identifiers: frozenset[str] = field(default_factory=frozenset)
    limit: int = 20
    character_limit: int = 20_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "intents", tuple(self.intents))
        object.__setattr__(self, "kinds", frozenset(self.kinds))
        object.__setattr__(self, "identifiers", frozenset(self.identifiers))
        if not 1 <= self.limit <= 100:
            raise ValueError("evidence limit must be between 1 and 100")
        if not 1_000 <= self.character_limit <= 100_000:
            raise ValueError("evidence character_limit must be between 1000 and 100000")


@dataclass(frozen=True)
class EvidenceBundle:
    scope: Scope
    items: tuple[InitializationEvidence, ...]
    truncated: bool = False

    def __post_init__(self) -> None:
        if any(item.scope != self.scope for item in self.items):
            raise ValueError("evidence bundle items must match its scope")
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence bundle ids must be unique")


@dataclass(frozen=True)
class InitializationLimits:
    candidate_limit: int = 20
    evidence_limit: int = 20
    evidence_character_limit: int = 20_000
    investigation_limit: int = 12

    def __post_init__(self) -> None:
        if not 1 <= self.candidate_limit <= 50:
            raise ValueError("candidate_limit must be between 1 and 50")
        if not 1 <= self.evidence_limit <= 100:
            raise ValueError("evidence_limit must be between 1 and 100")
        if not 1_000 <= self.evidence_character_limit <= 100_000:
            raise ValueError("evidence_character_limit must be between 1000 and 100000")
        if not 0 <= self.investigation_limit <= 50:
            raise ValueError("investigation_limit must be between 0 and 50")


@dataclass(frozen=True)
class InitializationCandidate:
    kind: str
    slug: str
    summary: str
    rationale: str
    future_decision: str
    invalidation: str
    evidence_ids: tuple[str, ...]
    paths: tuple[str, ...] = ()
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class CandidateAssessment:
    accepted: bool
    reasons: tuple[str, ...] = ()
