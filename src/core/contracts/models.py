from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.scope import Scope


@dataclass(frozen=True)
class ContractDocument:
    id: str
    format: str
    media_type: str
    digest: str
    size: int
    revision: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("contract document id cannot be empty")
        if not self.format:
            raise ValueError("contract document format cannot be empty")
        if not self.media_type:
            raise ValueError("contract document media_type cannot be empty")
        algorithm, separator, encoded = self.digest.partition(":")
        if not separator or not algorithm or not encoded:
            raise ValueError("contract document digest must be algorithm:value")
        if self.size < 0:
            raise ValueError("contract document size cannot be negative")


@dataclass(frozen=True)
class ContractPayload:
    document: ContractDocument
    content: bytes

    def __post_init__(self) -> None:
        if len(self.content) != self.document.size:
            raise ValueError("contract payload size does not match its document")


@dataclass(frozen=True)
class ContractElement:
    id: str
    kind: str
    name: str
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("contract element id cannot be empty")
        if not self.kind:
            raise ValueError("contract element kind cannot be empty")


@dataclass(frozen=True)
class ContractSnapshot:
    id: str
    document: ContractDocument
    elements: tuple[ContractElement, ...]
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("contract snapshot id cannot be empty")
        element_ids = [element.id for element in self.elements]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("contract snapshot element ids must be unique")


@dataclass(frozen=True)
class ContractEvidence:
    id: str
    kind: str
    source: str
    revision: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("contract evidence id cannot be empty")


@dataclass(frozen=True)
class ContractChange:
    id: str
    classification: str
    severity: str
    summary: str
    before_element_id: str | None = None
    after_element_id: str | None = None
    confidence: float = 1.0
    evidence: tuple[ContractEvidence, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("contract change id cannot be empty")
        if not self.classification:
            raise ValueError("contract change classification cannot be empty")
        if not self.severity:
            raise ValueError("contract change severity cannot be empty")
        if self.before_element_id is None and self.after_element_id is None:
            raise ValueError("contract change must identify an affected element")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("contract change confidence must be between 0 and 1")


@dataclass(frozen=True)
class ContractComparison:
    id: str
    before_snapshot_id: str
    after_snapshot_id: str
    compatible: bool
    changes: tuple[ContractChange, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("contract comparison id cannot be empty")
        if not self.before_snapshot_id:
            raise ValueError("comparison before_snapshot_id cannot be empty")
        if not self.after_snapshot_id:
            raise ValueError("comparison after_snapshot_id cannot be empty")
        change_ids = [change.id for change in self.changes]
        if len(change_ids) != len(set(change_ids)):
            raise ValueError("contract comparison change ids must be unique")


@dataclass(frozen=True)
class ContractQuery:
    scope: Scope
    document_ids: frozenset[str] = field(default_factory=frozenset)
    formats: frozenset[str] = field(default_factory=frozenset)
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.offset < 0:
            raise ValueError("offset must not be negative")


@dataclass(frozen=True)
class ContractResult:
    items: tuple[ContractSnapshot, ...]
    total: int
    has_more: bool
