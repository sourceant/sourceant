from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

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
    supported_predicates: frozenset[StructuralPredicate] = field(
        default_factory=frozenset
    )
    #: Every name the file binds anywhere, and the first line it binds it on.
    #: Bound somewhere is not the same as in scope here, which is why these
    #: are not facts: they answer a claim that carries a line, and only for a
    #: line after the binding.
    bindings: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceDecision:
    contradicted: bool
    reason: str = ""
