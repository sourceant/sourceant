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
