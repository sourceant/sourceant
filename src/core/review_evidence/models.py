from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field


class StructuralPredicate(str, Enum):
    IMPORTED = "IMPORTED"
    DEFINED = "DEFINED"


class ReviewClaim(BaseModel):
    subject: str = Field(..., min_length=1)
    predicate: StructuralPredicate
    expected: bool


@dataclass(frozen=True)
class StructuralFact:
    subject: str
    predicate: StructuralPredicate


@dataclass(frozen=True)
class FileEvidence:
    path: str
    language: str
    facts: frozenset[StructuralFact] = field(default_factory=frozenset)
    conditional_facts: frozenset[StructuralFact] = field(default_factory=frozenset)
    supported_predicates: frozenset[StructuralPredicate] = field(
        default_factory=frozenset
    )


@dataclass(frozen=True)
class EvidenceDecision:
    contradicted: bool
    reason: str = ""
