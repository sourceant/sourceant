from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol

from src.core.scope import Scope
from .models import Requirement

METHODS = frozenset({"test", "analysis", "inspection", "demonstration", "review"})
OUTCOMES = frozenset({"passed", "failed", "inconclusive", "waived"})


def required(value: str, name: str, maximum: int = 255):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must contain 1 to {maximum} characters")


def requirement_revision(requirement: Requirement) -> str:
    content = asdict(requirement)
    content.pop("status")
    content.pop("priority")
    properties = dict(content["properties"])
    for key in ("owner", "priority"):
        properties.pop(key, None)
    if properties.get("behavior"):
        properties["behavior"] = properties["behavior"]["revision"]
    content["properties"] = properties
    return sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    requirement_id: str
    statement: str
    method: str
    purpose: str = "verification"
    scenario_ids: tuple[str, ...] = ()
    active: bool = True
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        required(self.id, "Criterion id")
        required(self.requirement_id, "Requirement id")
        required(self.statement, "Acceptance criterion", 20000)
        if self.purpose not in {"verification", "validation"}:
            raise ValueError("Choose verification or validation")
        if self.method not in METHODS:
            raise ValueError(
                "Choose test, analysis, inspection, demonstration, or review"
            )
        for identifier in self.scenario_ids:
            required(identifier, "Scenario id")
        if len(set(self.scenario_ids)) != len(self.scenario_ids):
            raise ValueError("Scenario ids must be distinct")
        if len(self.scenario_ids) > 100:
            raise ValueError("A criterion may reference at most 100 scenarios")


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    type: str
    title: str
    source_ref: str
    observed_at: str
    result: Mapping[str, Any]
    subject_revision: str = ""
    environment: Mapping[str, Any] = field(default_factory=dict)
    producer: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for name, maximum in [
            ("id", 255),
            ("type", 128),
            ("title", 1000),
            ("source_ref", 2000),
        ]:
            required(getattr(self, name), name, maximum)
        required(self.observed_at, "Observation time", 64)
        observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("Evidence observation time needs a timezone")
        if not self.result:
            raise ValueError("Evidence needs recorded observations or results")
        if len(json.dumps(asdict(self)).encode()) > 100000:
            raise ValueError(
                "Evidence records must not exceed 100 KB; store large reports as artifacts"
            )


@dataclass(frozen=True)
class CriterionAssessment:
    id: str
    requirement_id: str
    criterion_id: str
    requirement_revision: str
    criterion_revision: str
    outcome: str
    rationale: str
    evidence_ids: tuple[str, ...]
    supersedes: str = ""

    def __post_init__(self):
        for name in (
            "id",
            "requirement_id",
            "criterion_id",
            "requirement_revision",
            "criterion_revision",
        ):
            required(getattr(self, name), name)
        required(self.rationale, "Assessment rationale", 20000)
        if self.outcome not in OUTCOMES:
            raise ValueError("Choose passed, failed, inconclusive, or waived")
        if not self.evidence_ids or len(self.evidence_ids) > 100:
            raise ValueError("An assessment needs 1 to 100 evidence records")
        for identifier in self.evidence_ids:
            required(identifier, "Evidence id")
        if len(self.supersedes) > 255:
            raise ValueError("Superseded assessment id must not exceed 255 characters")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Evidence ids must be distinct")


class AssuranceRepository(Protocol):
    def put_criterion(
        self,
        scope: Scope,
        criterion: AcceptanceCriterion,
        expected_revision: str,
        actor: str,
    ) -> dict: ...
    def record_evidence(
        self, scope: Scope, evidence: EvidenceRecord, actor: str
    ) -> dict: ...
    def assess(
        self, scope: Scope, assessment: CriterionAssessment, actor: str
    ) -> dict: ...
    def report(self, scope: Scope, requirement_id: str) -> dict: ...
    def get_evidence(self, scope: Scope, evidence_id: str) -> dict: ...
    def evidence(self, scope: Scope, limit: int = 50, offset: int = 0) -> dict: ...
    def history(
        self, scope: Scope, requirement_id: str, limit: int = 50, offset: int = 0
    ) -> dict: ...
