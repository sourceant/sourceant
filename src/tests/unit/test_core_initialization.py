import pytest

from src.core.initialization import (
    EvidenceQuery,
    EvidenceReference,
    InMemoryInitializationEvidenceReader,
    InitializationEvidence,
    InitializationLimits,
)
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
