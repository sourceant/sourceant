"""How much a finding matters, and what decides it."""

from src.models.code_review import (
    CodeSuggestion,
    Impact,
    Reach,
    Severity,
    Side,
    SuggestionCategory,
    Verdict,
    is_nitpick,
    severity_of,
    summary_from,
)
from src.plugins.builtin.code_reviewer.reviewing import verdict_from


def finding(**answered) -> CodeSuggestion:
    return CodeSuggestion(
        **{
            "file_name": "src/offers.py",
            "start_line": 1,
            "end_line": 1,
            "side": Side.RIGHT,
            "comment": "a finding",
            "category": SuggestionCategory.BUG,
            "suggested_code": None,
            **answered,
        }
    )


def test_two_bugs_are_ranked_by_what_they_do_and_not_by_being_bugs():
    kept = summary_from(
        [
            finding(
                comment="drops rows for every caller",
                reach=Reach.ANYONE,
                impact=Impact.CORRUPTION,
            ),
            finding(
                comment="unbounded percent, refused by the provider",
                reach=Reach.OPERATOR,
                impact=Impact.REJECTED,
            ),
        ]
    )

    assert kept.critical_issues == ["drops rows for every caller"]
    assert kept.minor_suggestions == ["unbounded percent, refused by the provider"]


def test_a_bad_value_the_next_layer_refuses_does_not_request_changes():
    contained = finding(reach=Reach.OPERATOR, impact=Impact.REJECTED)

    assert severity_of(contained) is Severity.ADVISORY
    assert verdict_from([contained]) is Verdict.COMMENT


def test_saying_something_is_not_an_injection_risk_does_not_request_changes():
    said = finding(
        comment="The column list is an allowlist, so this is not an injection risk.",
        category=SuggestionCategory.CLARITY,
        reach=Reach.OPERATOR,
        impact=Impact.NONE,
    )

    assert verdict_from([said]) is Verdict.COMMENT


def test_an_unauthenticated_caller_reading_what_they_should_not_blocks():
    exposed = finding(
        category=SuggestionCategory.SECURITY,
        reach=Reach.ANYONE,
        impact=Impact.DISCLOSURE,
    )

    assert severity_of(exposed) is Severity.BLOCKING
    assert verdict_from([exposed]) is Verdict.REQUEST_CHANGES


def test_a_finding_that_answered_neither_question_is_ranked_by_category():
    assert severity_of(finding(category=SuggestionCategory.BUG)) is Severity.BLOCKING
    assert severity_of(finding(category=SuggestionCategory.STYLE)) is Severity.ADVISORY


def test_a_finding_with_no_runtime_consequence_is_a_nitpick():
    assert is_nitpick(finding(reach=Reach.OPERATOR, impact=Impact.NONE))
    assert not is_nitpick(finding(reach=Reach.ANYONE, impact=Impact.DEGRADED))


def test_a_finding_that_answered_neither_question_is_filtered_by_category():
    assert is_nitpick(finding(category=SuggestionCategory.STYLE))
    assert not is_nitpick(finding(category=SuggestionCategory.PERFORMANCE))


def test_every_pair_of_answers_has_a_severity():
    for reach in Reach:
        for impact in Impact:
            assert isinstance(
                severity_of(finding(reach=reach, impact=impact)), Severity
            )


def test_the_ranking_questions_are_required_to_answer_with():
    required = set(CodeSuggestion.model_json_schema()["required"])

    assert {"reach", "impact"} <= required
