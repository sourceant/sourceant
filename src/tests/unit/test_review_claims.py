"""A claim a model does not make is a claim nothing can check.

Two halves: the schema has to require claims, and the evidence has to record
the names a claim can be about.
"""

from src.core.review_evidence import (
    CachedChangedFileEvidenceReader,
    StructuralReviewEvidenceValidator,
)
from src.core.review_evidence.models import (
    ReviewClaim,
    StructuralFact,
    StructuralPredicate,
)
from src.models.code_review import CodeReview, CodeSuggestion, Side

MODULE = '''"""A file with a constant, an import and a function."""

import json

_DIFF = """diff --git a/x b/x"""
LIMIT: int = 20


def read(path):
    return json.loads(path)
'''


def evidence_for(content: str, path: str = "sample.py"):
    return CachedChangedFileEvidenceReader(lambda _: content).read(path)


def test_a_model_must_answer_with_claims():
    """Optional, a model omits them, and every assertion goes unchecked."""
    schema = CodeReview.model_json_schema()["$defs"]["CodeSuggestion"]

    assert "claims" in schema["required"]


def test_nothing_here_has_to_state_claims_to_build_a_suggestion():
    """The demand is on what answers the schema, not on this codebase."""
    made = CodeSuggestion(
        file_name="a.py",
        start_line=1,
        end_line=1,
        side=Side.RIGHT,
        comment="c",
        category=None,
        suggested_code="x",
    )

    assert made.claims == []


def test_a_name_bound_at_the_left_margin_is_defined():
    """A constant is a definition. The parser reports only functions."""
    facts = {one.subject for one in evidence_for(MODULE).facts}

    assert "_DIFF" in facts
    assert "LIMIT" in facts, "an annotated assignment counts too"


def test_what_the_parser_already_found_is_still_there():
    facts = {one.subject for one in evidence_for(MODULE).facts}

    assert "read" in facts
    assert "json" in facts


def test_a_claim_the_file_contradicts_takes_its_suggestion_with_it():
    said = [
        ReviewClaim(
            subject="_DIFF", predicate=StructuralPredicate.DEFINED, expected=False
        )
    ]

    decision = StructuralReviewEvidenceValidator().validate(said, evidence_for(MODULE))

    assert decision.contradicted


def test_a_claim_the_file_agrees_with_is_left_alone():
    said = [
        ReviewClaim(
            subject="missing", predicate=StructuralPredicate.DEFINED, expected=False
        )
    ]

    decision = StructuralReviewEvidenceValidator().validate(said, evidence_for(MODULE))

    assert not decision.contradicted


def test_a_name_inside_a_function_is_not_a_module_level_binding():
    """Indented bindings belong to another scope."""
    inside = "def f():\n    LOCAL = 1\n    return LOCAL\n"

    facts = {one.subject for one in evidence_for(inside).facts}

    assert "LOCAL" not in facts


def test_a_language_with_no_analysis_says_so_rather_than_guessing():
    assert evidence_for("x = 1", path="sample.unknownlang") is None


def test_javascript_bindings_are_read_too():
    facts = {
        one.subject
        for one in evidence_for(
            "export const LIMIT = 20\nlet other = 1\n", "a.js"
        ).facts
    }

    assert {"LIMIT", "other"} <= facts


def test_a_fact_is_the_pair_of_a_name_and_what_is_claimed_about_it():
    facts = evidence_for(MODULE).facts

    assert StructuralFact("_DIFF", StructuralPredicate.DEFINED) in facts
