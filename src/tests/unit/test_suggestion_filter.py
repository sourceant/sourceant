import pytest

from src.models.code_review import CodeSuggestion, SuggestionCategory, Side
from src.utils.suggestion_filter import SuggestionFilter


@pytest.fixture
def suggestion_filter():
    return SuggestionFilter()


def _make_suggestion(
    comment: str = "Fix the bug here",
    suggested_code: str = "fixed_code()",
    existing_code: str = "broken_code()",
) -> CodeSuggestion:
    return CodeSuggestion(
        file_name="test.py",
        start_line=1,
        end_line=1,
        side=Side.RIGHT,
        comment=comment,
        category=SuggestionCategory.BUG,
        suggested_code=suggested_code,
        existing_code=existing_code,
    )


def test_purely_positive_comment_filtered(suggestion_filter):
    suggestion = _make_suggestion(comment="Great implementation!")
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_negative_comment_kept(suggestion_filter):
    suggestion = _make_suggestion(comment="This has a bug in the loop logic.")
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 1
    assert len(removed) == 0


def test_mixed_comment_kept(suggestion_filter):
    suggestion = _make_suggestion(
        comment="Good approach, but consider null handling for edge cases."
    )
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 1
    assert len(removed) == 0


def test_vader_detectable_positive_not_in_regex_filtered(suggestion_filter):
    suggestion = _make_suggestion(comment="Love the clean approach here!")
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_empty_comment_filtered(suggestion_filter):
    suggestion = _make_suggestion(comment="")
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_no_suggested_code_filtered(suggestion_filter):
    suggestion = _make_suggestion(
        comment="This should be refactored.", suggested_code=None
    )
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_identical_code_filtered(suggestion_filter):
    suggestion = _make_suggestion(
        comment="This should be improved.",
        suggested_code="same_code()",
        existing_code="same_code()",
    )
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_informational_neutral_comment_filtered(suggestion_filter):
    suggestion = _make_suggestion(comment="This function returns an integer.")
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_missing_existing_code_dropped_by_default(suggestion_filter, monkeypatch):
    monkeypatch.setattr(
        "src.utils.suggestion_filter.REVIEW_MISSING_EXISTING_CODE_POLICY", "drop"
    )
    suggestion = _make_suggestion(
        comment="Fix the bug here", existing_code=None, suggested_code="fixed()"
    )
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_missing_existing_code_warn_policy_keeps(suggestion_filter, monkeypatch):
    monkeypatch.setattr(
        "src.utils.suggestion_filter.REVIEW_MISSING_EXISTING_CODE_POLICY", "warn"
    )
    suggestion = _make_suggestion(
        comment="Fix the bug here", existing_code=None, suggested_code="fixed()"
    )
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 1
    assert len(removed) == 0


def test_missing_existing_code_keep_policy_keeps(suggestion_filter, monkeypatch):
    monkeypatch.setattr(
        "src.utils.suggestion_filter.REVIEW_MISSING_EXISTING_CODE_POLICY", "keep"
    )
    suggestion = _make_suggestion(
        comment="Fix the bug here", existing_code=None, suggested_code="fixed()"
    )
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 1
    assert len(removed) == 0


def test_invalid_existing_code_policy_falls_back_to_drop(
    suggestion_filter, monkeypatch
):
    monkeypatch.setattr(
        "src.utils.suggestion_filter.REVIEW_MISSING_EXISTING_CODE_POLICY", "invalid"
    )
    suggestion = _make_suggestion(
        comment="Fix the bug here", existing_code=None, suggested_code="fixed()"
    )
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 0
    assert len(removed) == 1


def test_positive_with_actionable_verb_kept(suggestion_filter):
    suggestion = _make_suggestion(comment="Nice approach, add a null check for safety.")
    kept, removed = suggestion_filter.filter_suggestions([suggestion])
    assert len(kept) == 1
    assert len(removed) == 0


def test_completed_change_praise_with_negative_keyword_filtered(suggestion_filter):
    suggestion = _make_suggestion(
        comment=(
            "The change from `Number(targetEl.dataset.assetId)` to "
            "`targetEl?.dataset.assetId ?? null` correctly reflects the new "
            "string-based ID system. This is a good simplification, removing "
            "an unnecessary type conversion."
        )
    )

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == []
    assert removed == [suggestion]


def test_completed_change_description_with_unresolved_problem_kept(suggestion_filter):
    suggestion = _make_suggestion(
        comment=(
            "This change correctly preserves the string ID, but it still fails "
            "when the target element is missing. Add a null guard."
        )
    )

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == [suggestion]
    assert removed == []


@pytest.mark.parametrize(
    "comment",
    [
        "This is a good simplification that removes an unnecessary type conversion.",
        "Nice catch removing the redundant null check here.",
        "The refactor here is clean and removes an unnecessary conversion.",
    ],
)
def test_completed_change_praise_variants_filtered(suggestion_filter, comment):
    suggestion = _make_suggestion(comment=comment)

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == []
    assert removed == [suggestion]


def test_praise_followed_by_separate_unresolved_problem_kept(suggestion_filter):
    suggestion = _make_suggestion(
        comment=(
            "The change correctly removes the conversion. "
            "It introduces a null dereference at line 40."
        )
    )

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == [suggestion]
    assert removed == []


def test_completed_change_praise_with_same_sentence_harm_kept(suggestion_filter):
    suggestion = _make_suggestion(
        comment=(
            "The change correctly removes the conversion and introduces a "
            "null dereference at line 40."
        )
    )

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == [suggestion]
    assert removed == []


def test_finding_in_a_lower_case_sentence_kept(suggestion_filter):
    """A sentence need not begin in upper case, and the finding in one was being
    read as part of the praise before it."""
    suggestion = _make_suggestion(
        comment="Correctly removed the cast. it now throws on null input."
    )

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == [suggestion]
    assert removed == []


def test_abbreviation_does_not_end_a_sentence(suggestion_filter):
    suggestion = _make_suggestion(
        comment=(
            "The change correctly removes the cast e.g. the Number() call. "
            "It now crashes on null."
        )
    )

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == [suggestion]
    assert removed == []


def test_fault_reported_in_the_plural_kept(suggestion_filter):
    for comment in (
        "This crashes on null input.",
        "This raises exceptions when the list is empty.",
        "This introduces regressions in the mapper.",
    ):
        suggestion = _make_suggestion(comment=comment)

        kept, removed = suggestion_filter.filter_suggestions([suggestion])

        assert kept == [suggestion], comment
        assert removed == [], comment


def test_runtime_fault_without_an_actionable_verb_kept(suggestion_filter):
    """A reviewer reports what goes wrong at runtime without always naming it a
    bug or asking for a change."""
    for comment in (
        "Nicely simplified. This leaks the file handle.",
        "It hangs when the queue is empty.",
        "This panics on an empty slice.",
    ):
        suggestion = _make_suggestion(comment=comment)

        kept, removed = suggestion_filter.filter_suggestions([suggestion])

        assert kept == [suggestion], comment
        assert removed == [], comment


def test_praise_for_a_completed_change_still_filtered(suggestion_filter):
    for comment in (
        "This is a good simplification that removes an unnecessary type conversion.",
        "Nice catch removing the redundant null check here.",
        "The refactor here is clean and removes an unnecessary conversion.",
    ):
        suggestion = _make_suggestion(comment=comment)

        kept, removed = suggestion_filter.filter_suggestions([suggestion])

        assert kept == [], comment
        assert removed == [suggestion], comment


def test_inline_code_does_not_end_a_sentence(suggestion_filter):
    """A comment quotes the code it is about, and that code carries the same
    characters a sentence ends with."""
    suggestion = _make_suggestion(
        comment="Correctly uses `a ?? b` now. it still leaks the handle."
    )

    kept, removed = suggestion_filter.filter_suggestions([suggestion])

    assert kept == [suggestion]
    assert removed == []
