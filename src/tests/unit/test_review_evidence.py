from src.core.review_evidence import (
    CachedChangedFileEvidenceReader,
    ReviewClaim,
    StructuralPredicate,
    StructuralReviewEvidenceValidator,
)


def test_structure_rejects_when_any_factual_claim_is_contradicted():
    reader = CachedChangedFileEvidenceReader(
        lambda path: "import logging\n\nlogger = logging.getLogger(__name__)\n"
    )

    decision = StructuralReviewEvidenceValidator().validate(
        [
            ReviewClaim(
                subject="logging",
                predicate=StructuralPredicate.IMPORTED,
                expected=False,
            ),
            ReviewClaim(
                subject="logger",
                predicate=StructuralPredicate.DEFINED,
                expected=False,
            ),
        ],
        reader.read("src/api/routes/topology.py"),
    )

    assert decision.contradicted is True
    assert decision.reason == "post-change structure contradicts a factual claim"


def test_structure_keeps_a_claim_that_matches_the_file():
    reader = CachedChangedFileEvidenceReader(
        lambda path: "def handle():\n    return 1\n"
    )

    decision = StructuralReviewEvidenceValidator().validate(
        [
            ReviewClaim(
                subject="logging",
                predicate=StructuralPredicate.IMPORTED,
                expected=False,
            )
        ],
        reader.read("handler.py"),
    )

    assert decision.contradicted is False


def test_structure_rejects_compound_claim_when_another_claim_is_contradicted():
    reader = CachedChangedFileEvidenceReader(lambda path: "import logging\n")

    decision = StructuralReviewEvidenceValidator().validate(
        [
            ReviewClaim(
                subject="logging",
                predicate=StructuralPredicate.IMPORTED,
                expected=False,
            ),
            ReviewClaim(
                subject="logger",
                predicate=StructuralPredicate.DEFINED,
                expected=False,
            ),
        ],
        reader.read("handler.py"),
    )

    assert decision.contradicted is True


def test_structure_does_not_treat_local_assignments_as_file_definitions():
    reader = CachedChangedFileEvidenceReader(
        lambda path: "def unrelated():\n    logger = object()\n"
    )

    decision = StructuralReviewEvidenceValidator().validate(
        [
            ReviewClaim(
                subject="logger",
                predicate=StructuralPredicate.DEFINED,
                expected=False,
            )
        ],
        reader.read("handler.py"),
    )

    assert decision.contradicted is False


def test_changed_file_evidence_is_cached_and_bounded():
    calls = []

    def read(path):
        calls.append(path)
        return "import logging\n"

    reader = CachedChangedFileEvidenceReader(read, character_limit=10)

    assert reader.read("handler.py") is None
    assert reader.read("handler.py") is None
    assert calls == ["handler.py"]


def test_changed_file_evidence_failure_does_not_reject_a_claim():
    def fail(path):
        raise ValueError("content unavailable")

    reader = CachedChangedFileEvidenceReader(fail)

    decision = StructuralReviewEvidenceValidator().validate(
        [
            ReviewClaim(
                subject="logging",
                predicate=StructuralPredicate.IMPORTED,
                expected=False,
            )
        ],
        reader.read("handler.py"),
    )

    assert decision.contradicted is False


def test_structure_keeps_suggestions_without_machine_checkable_claims():
    reader = CachedChangedFileEvidenceReader(lambda path: "import logging\n")

    decision = StructuralReviewEvidenceValidator().validate(
        [], reader.read("handler.py")
    )

    assert decision.contradicted is False
