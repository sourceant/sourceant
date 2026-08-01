import pytest

from src.core.review_evidence import (
    CachedChangedFileEvidenceReader,
    ReviewClaim,
    StructuralFact,
    StructuralPredicate,
    StructuralReviewEvidenceValidator,
)


@pytest.mark.parametrize(
    ("path", "content", "import_name", "member_name"),
    [
        (
            "service.js",
            'import logger from "./logger.js";\n' "export class Service { run() {} }",
            "logger",
            "Service.run",
        ),
        (
            "service.ts",
            'import type { Logger as LogType } from "./logger";\n'
            "export class Service { run(): void {} }",
            "LogType",
            "Service.run",
        ),
        (
            "service.php",
            "<?php\nuse App\\Logger;\nclass Service { "
            "public function run(): void {} }",
            "Logger",
            "Service.run",
        ),
        (
            "Service.java",
            "import java.util.List; class Service { void run() {} }",
            "List",
            "Service.run",
        ),
    ],
)
def test_structure_verifies_common_language_imports_and_members(
    path, content, import_name, member_name
):
    evidence = CachedChangedFileEvidenceReader(lambda candidate: content).read(path)
    validator = StructuralReviewEvidenceValidator()

    assert evidence is not None
    for subject, predicate in (
        (import_name, StructuralPredicate.IMPORTED),
        (member_name, StructuralPredicate.DEFINED),
    ):
        decision = validator.validate(
            [ReviewClaim(subject=subject, predicate=predicate, expected=False)],
            evidence,
        )

        assert decision.contradicted is True


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


def test_structure_uses_qualified_class_members():
    reader = CachedChangedFileEvidenceReader(
        lambda path: "class Service:\n    registry = {}\n\n    def run(self):\n        pass\n"
    )
    evidence = reader.read("handler.py")
    validator = StructuralReviewEvidenceValidator()

    for subject in ("Service.registry", "Service.run"):
        present = validator.validate(
            [
                ReviewClaim(
                    subject=subject,
                    predicate=StructuralPredicate.DEFINED,
                    expected=True,
                )
            ],
            evidence,
        )
        missing = validator.validate(
            [
                ReviewClaim(
                    subject=subject,
                    predicate=StructuralPredicate.DEFINED,
                    expected=False,
                )
            ],
            evidence,
        )

        assert present.contradicted is False
        assert missing.contradicted is True


def test_structure_preserves_positive_claim_when_presence_is_not_proven():
    reader = CachedChangedFileEvidenceReader(lambda path: "class Service:\n    pass\n")

    decision = StructuralReviewEvidenceValidator().validate(
        [
            ReviewClaim(
                subject="Service.run",
                predicate=StructuralPredicate.DEFINED,
                expected=True,
            )
        ],
        reader.read("handler.py"),
    )

    assert decision.contradicted is False


def test_structure_marks_control_flow_facts_as_conditional():
    reader = CachedChangedFileEvidenceReader(
        lambda path: (
            "try:\n"
            "    import orjson\n"
            "except ImportError:\n"
            "    orjson = None\n"
            "if TYPE_CHECKING:\n"
            "    FLAG = True\n"
        )
    )

    evidence = reader.read("handler.py")

    assert evidence is not None
    assert (
        StructuralFact("orjson", StructuralPredicate.IMPORTED)
        in evidence.conditional_facts
    )
    assert (
        StructuralFact("FLAG", StructuralPredicate.DEFINED)
        in evidence.conditional_facts
    )


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
