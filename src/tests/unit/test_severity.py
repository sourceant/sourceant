"""How much a finding matters, and what decides it."""

import pytest

from src.models.code_review import (
    Blast,
    Certainty,
    CodeSuggestion,
    Impact,
    Severity,
    Side,
    SuggestionCategory,
    Trigger,
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


def ranked(trigger, blast, impact, certainty=Certainty.ALWAYS, **rest):
    return finding(
        trigger=trigger, blast=blast, impact=impact, certainty=certainty, **rest
    )


@pytest.mark.parametrize(
    "answers, expected",
    [
        # A bad value on an admin form that the payment provider refuses.
        ((Trigger.OPERATOR, Blast.ONE, Impact.REJECTED), Severity.NIT),
        # An injection anybody can reach.
        ((Trigger.ANYONE, Blast.EVERYONE, Impact.DISCLOSURE), Severity.BLOCKING),
        # A deploy that drops a column something still reads.
        ((Trigger.OPERATOR, Blast.EVERYONE, Impact.CORRUPTION), Severity.BLOCKING),
        # An endpoint that dies on the caller's own malformed input.
        ((Trigger.ANYONE, Blast.ONE, Impact.CRASH), Severity.ADVISORY),
        # An injection only an operator can reach, who could already do it.
        ((Trigger.OPERATOR, Blast.EVERYONE, Impact.ESCALATION), Severity.ADVISORY),
        # The same, reached by a release rather than by a person at a keyboard.
        ((Trigger.DEPLOY, Blast.EVERYONE, Impact.DISCLOSURE), Severity.ADVISORY),
    ],
)
def test_the_answers_decide_the_severity(answers, expected):
    assert severity_of(ranked(*answers)) is expected


@pytest.mark.parametrize("harmless", [Impact.REJECTED, Impact.NONE])
def test_reaching_everybody_does_not_worsen_what_leaves_nothing_behind(harmless):
    """A refusal refuses the same way however many people arrive."""
    everywhere = ranked(Trigger.ANYONE, Blast.EVERYONE, harmless)
    once = ranked(Trigger.ANYONE, Blast.MANY, harmless)

    assert severity_of(everywhere) is severity_of(once)
    assert severity_of(everywhere) is not Severity.BLOCKING


def test_a_finding_resting_on_an_assumption_is_a_nitpick():
    guessed = ranked(
        Trigger.ANYONE, Blast.ONE, Impact.CRASH, certainty=Certainty.POSSIBLE
    )

    assert severity_of(guessed) is Severity.NIT
    assert is_nitpick(guessed)


def test_naming_when_it_happens_softens_it_by_one():
    always = ranked(Trigger.ANYONE, Blast.MANY, Impact.CRASH)
    sometimes = ranked(
        Trigger.ANYONE, Blast.MANY, Impact.CRASH, certainty=Certainty.CONDITIONAL
    )

    assert severity_of(always) is Severity.BLOCKING
    assert severity_of(sometimes) is Severity.ADVISORY


def test_a_defect_on_a_line_nothing_reaches_is_a_nitpick():
    """It has no cause to name, so what it has is no consequence."""
    dead = ranked(Trigger.ANYONE, Blast.NOBODY, Impact.NONE)

    assert severity_of(dead) is Severity.NIT
    assert is_nitpick(dead)


@pytest.mark.parametrize(
    "unattended",
    [Trigger.AUTOMATION, Trigger.EVENT, Trigger.SERVICE, Trigger.ENVIRONMENT],
)
def test_a_cause_with_no_authority_does_not_borrow_an_operator_s(unattended):
    """A cron is not an administrator who could already do this.

    Without somewhere of its own to go each of these would be filed as an
    operator, and the softening that answers for a person would answer for
    it too.
    """
    by_itself = ranked(unattended, Blast.EVERYONE, Impact.DISCLOSURE)
    by_hand = ranked(Trigger.OPERATOR, Blast.EVERYONE, Impact.DISCLOSURE)

    assert severity_of(by_itself) is Severity.BLOCKING
    assert severity_of(by_hand) is Severity.ADVISORY


def test_two_bugs_are_ranked_by_what_they_do_and_not_by_being_bugs():
    kept = summary_from(
        [
            ranked(
                Trigger.ANYONE,
                Blast.EVERYONE,
                Impact.CORRUPTION,
                comment="drops rows for every caller",
            ),
            ranked(
                Trigger.OPERATOR,
                Blast.ONE,
                Impact.REJECTED,
                comment="unbounded percent, refused by the provider",
            ),
        ]
    )

    assert kept.critical_issues == ["drops rows for every caller"]
    assert kept.minor_suggestions == ["unbounded percent, refused by the provider"]


def test_a_bad_value_the_next_layer_refuses_does_not_request_changes():
    contained = ranked(Trigger.OPERATOR, Blast.ONE, Impact.REJECTED)

    assert verdict_from([contained]) is Verdict.COMMENT


def test_saying_something_is_not_an_injection_risk_does_not_request_changes():
    said = ranked(
        Trigger.OPERATOR,
        Blast.NOBODY,
        Impact.NONE,
        category=SuggestionCategory.CLARITY,
        comment="The column list is an allowlist, so this is not an injection risk.",
    )

    assert verdict_from([said]) is Verdict.COMMENT


def test_an_unauthenticated_caller_reading_what_they_should_not_blocks():
    exposed = ranked(
        Trigger.ANYONE,
        Blast.EVERYONE,
        Impact.DISCLOSURE,
        category=SuggestionCategory.SECURITY,
    )

    assert verdict_from([exposed]) is Verdict.REQUEST_CHANGES


def test_a_finding_that_did_not_answer_is_ranked_by_category():
    assert severity_of(finding(category=SuggestionCategory.BUG)) is Severity.BLOCKING
    assert severity_of(finding(category=SuggestionCategory.STYLE)) is Severity.ADVISORY


def test_part_of_an_answer_is_ranked_and_filtered_as_none_of_it():
    neither = finding(category=SuggestionCategory.STYLE)
    partial = [
        finding(category=SuggestionCategory.STYLE, impact=Impact.NONE),
        finding(category=SuggestionCategory.STYLE, trigger=Trigger.ANYONE),
        finding(
            category=SuggestionCategory.STYLE,
            trigger=Trigger.ANYONE,
            blast=Blast.ONE,
            impact=Impact.NONE,
        ),
    ]

    for half in partial:
        assert severity_of(half) is severity_of(neither)
        assert is_nitpick(half) is is_nitpick(neither)


def test_a_finding_with_no_runtime_consequence_is_a_nitpick():
    assert is_nitpick(ranked(Trigger.OPERATOR, Blast.NOBODY, Impact.NONE))
    assert not is_nitpick(ranked(Trigger.ANYONE, Blast.MANY, Impact.DEGRADED))


def test_a_finding_that_did_not_answer_is_filtered_by_category():
    assert is_nitpick(finding(category=SuggestionCategory.STYLE))
    assert not is_nitpick(finding(category=SuggestionCategory.PERFORMANCE))


def test_every_set_of_answers_has_a_severity():
    for trigger in Trigger:
        for blast in Blast:
            for impact in Impact:
                for certainty in Certainty:
                    assert isinstance(
                        severity_of(ranked(trigger, blast, impact, certainty)),
                        Severity,
                    )


def test_the_ranking_questions_are_required_to_answer_with():
    required = set(CodeSuggestion.model_json_schema()["required"])

    assert {"trigger", "blast", "impact", "certainty"} <= required
