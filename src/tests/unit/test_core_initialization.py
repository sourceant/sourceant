import pytest

from src.core.initialization import (
    DefaultInitializationCandidatePolicy,
    EvidenceQuery,
    EvidenceReference,
    InMemoryInitializationEvidenceReader,
    InitializationCandidate,
    InitializationEvidence,
    InitializationLimits,
)
from src.core.settings import get
from src.core.scope import Scope

SCOPE = Scope.from_mapping({"repository": "sourceant"})


def test_discovers_bounded_matching_evidence():
    reader = InMemoryInitializationEvidenceReader(
        (
            InitializationEvidence(
                SCOPE,
                "auth",
                "constraint",
                "Requests require an authorized scope",
                references=(EvidenceReference("code", "auth", path="auth.py"),),
            ),
            InitializationEvidence(SCOPE, "queue", "fact", "Redis queues jobs"),
        )
    )

    result = reader.discover(
        EvidenceQuery(SCOPE, intents=("authorized",), limit=10),
        InitializationLimits(evidence_limit=5),
    )

    assert tuple(item.id for item in result.items) == ("auth",)
    assert not result.truncated


def test_marks_result_truncated_at_item_limit():
    reader = InMemoryInitializationEvidenceReader(
        (
            InitializationEvidence(SCOPE, "one", "fact", "One"),
            InitializationEvidence(SCOPE, "two", "fact", "Two"),
        )
    )

    result = reader.discover(
        EvidenceQuery(SCOPE, limit=2),
        InitializationLimits(evidence_limit=1),
    )

    assert tuple(item.id for item in result.items) == ("one",)
    assert result.truncated


def test_rejects_partial_evidence_line_range():
    with pytest.raises(ValueError, match="line range must be complete"):
        EvidenceReference("code", "auth", start_line=2)


def test_does_not_return_evidence_from_another_scope():
    other_scope = Scope.from_mapping({"repository": "other"})
    reader = InMemoryInitializationEvidenceReader(
        (InitializationEvidence(other_scope, "private", "fact", "Other scope"),)
    )

    result = reader.discover(EvidenceQuery(SCOPE), InitializationLimits())

    assert result.items == ()


def test_truncates_evidence_content_to_character_limit():
    reader = InMemoryInitializationEvidenceReader(
        (
            InitializationEvidence(
                SCOPE,
                "large",
                "fact",
                "Large item",
                "x" * 2_000,
            ),
        )
    )

    result = reader.discover(
        EvidenceQuery(SCOPE, character_limit=1_000),
        InitializationLimits(evidence_character_limit=1_000),
    )

    assert len(result.items[0].summary) + len(result.items[0].content) == 1_000
    assert result.truncated


def test_discovery_enforces_the_configured_character_budget():
    reader = InMemoryInitializationEvidenceReader(
        (
            InitializationEvidence(
                SCOPE,
                "large",
                "fact",
                "Large item",
                "x" * 50_000,
            ),
        )
    )

    result = reader.discover(
        EvidenceQuery(SCOPE, character_limit=100_000),
        InitializationLimits(evidence_character_limit=1_000),
    )

    assert len(result.items[0].summary) + len(result.items[0].content) == 1_000
    assert result.truncated


def test_investigation_enforces_configured_limits():
    reader = InMemoryInitializationEvidenceReader(
        tuple(
            InitializationEvidence(SCOPE, str(index), "fact", "Evidence")
            for index in range(4)
        )
    )

    result = reader.investigate(
        EvidenceQuery(
            SCOPE,
            identifiers=frozenset({"0", "1", "2", "3"}),
            limit=4,
        ),
        InitializationLimits(evidence_limit=3, investigation_limit=2),
    )

    assert tuple(item.id for item in result.items) == ("0", "1")


def test_evidence_properties_are_immutable_and_not_part_of_identity():
    first = InitializationEvidence(
        SCOPE,
        "evidence",
        "fact",
        "Evidence",
        properties={"nested": {"values": [1, 2]}},
    )
    second = InitializationEvidence(
        SCOPE,
        "evidence",
        "fact",
        "Evidence",
        properties={"nested": {"values": [1, 2]}},
    )

    with pytest.raises(TypeError):
        first.properties["changed"] = True
    with pytest.raises(TypeError):
        first.properties["nested"]["changed"] = True

    assert hash(first) == hash(second)
    assert first == second


@pytest.mark.parametrize(
    "summary",
    (
        "The service is written in Go",
        "Dependencies include Dramatiq",
        "This repository contains Terraform modules",
        "The backend is powered by Django",
        "Configuration lives in config/settings",
        "The scheduler runs jobs nightly",
    ),
)
def test_candidate_policy_rejects_repository_inventory(summary):
    candidate = InitializationCandidate(
        "decision",
        "repository-fact",
        summary,
        "The declaration and dependency manifest show this repository fact.",
        "Future changes should continue following the observed repository fact.",
        "The repository no longer contains the observed declaration.",
        ("symbol",),
    )

    result = DefaultInitializationCandidatePolicy().assess(candidate)

    assert not result.accepted
    assert result.reasons == ("summary describes repository inventory",)


def test_candidate_policy_accepts_an_actionable_invariant():
    candidate = InitializationCandidate(
        "constraint",
        "delivery-before-checkpoint",
        "Advance the checkpoint only after external delivery succeeds",
        "Earlier advancement can permanently hide work after delivery fails.",
        "New delivery paths must persist success before advancing the checkpoint.",
        "Delivery and checkpoint persistence become one atomic operation.",
        ("worker.delivery.checkpoint",),
    )

    assert DefaultInitializationCandidatePolicy().assess(candidate).accepted


def test_initialization_limits_are_exposed_as_settings():
    candidate_limit = get("initialization.candidate_limit")

    assert candidate_limit.default == 20
    assert candidate_limit.scopes == ("repository", "organization")
    assert get("initialization.evidence_limit").maximum == 100
    assert get("initialization.evidence_character_limit").default == 20_000
    assert get("initialization.investigation_limit").minimum == 0
