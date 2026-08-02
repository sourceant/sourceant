import pytest

from src.core.review_evidence import (
    CachedChangedFileEvidenceReader,
    ReviewClaim,
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
        (
            "service.py",
            "import logging\nclass Service:\n    def run(self):\n        pass\n",
            "logging",
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


def test_structure_fallback_accepts_detected_languages_without_scip_indexers():
    evidence = CachedChangedFileEvidenceReader(
        lambda path: "package service\n\nfunc Run() {}\n"
    ).read("service.go")

    assert evidence is not None
    assert evidence.language == "go"
    assert StructuralPredicate.DEFINED in evidence.supported_predicates
    assert StructuralPredicate.IMPORTED not in evidence.supported_predicates


@pytest.mark.parametrize(
    ("path", "content", "definition"),
    [
        (
            "main.rs",
            "pub struct Service; impl Service { pub fn run(&self) {} }",
            "Service.run",
        ),
        (
            "main.go",
            "package main\ntype Service struct{}\nfunc (s Service) Run() {}",
            "Run",
        ),
        (
            "Main.cs",
            "class Service { public void Run() {} }",
            "Service.Run",
        ),
        (
            "main.rb",
            "class Service\n  def run\n  end\nend",
            "Service.run",
        ),
        (
            "Main.kt",
            "class Service { fun run() {} }",
            "Service",
        ),
    ],
)
def test_structure_extracts_definitions_from_systems_and_application_languages(
    path, content, definition
):
    evidence = CachedChangedFileEvidenceReader(lambda candidate: content).read(path)

    assert evidence is not None
    assert StructuralPredicate.DEFINED in evidence.supported_predicates
    assert any(fact.subject == definition for fact in evidence.facts)


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("main.c", "struct Service {}; void run(void) {}"),
        ("main.cpp", "class Service { public: void run() {} };"),
    ],
)
def test_structure_ignores_unnamed_parser_nodes(path, content):
    evidence = CachedChangedFileEvidenceReader(lambda candidate: content).read(path)

    assert evidence is not None
    assert all(fact.subject for fact in evidence.facts)


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


def test_structure_uses_qualified_class_methods():
    reader = CachedChangedFileEvidenceReader(
        lambda path: "class Service:\n    def run(self):\n        pass\n"
    )
    evidence = reader.read("handler.py")
    validator = StructuralReviewEvidenceValidator()

    present = validator.validate(
        [
            ReviewClaim(
                subject="Service.run",
                predicate=StructuralPredicate.DEFINED,
                expected=True,
            )
        ],
        evidence,
    )
    missing = validator.validate(
        [
            ReviewClaim(
                subject="Service.run",
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
