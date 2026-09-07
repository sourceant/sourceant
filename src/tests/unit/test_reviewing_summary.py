"""What a review says about a change, when its findings do not survive."""

from src.models.code_review import CodeReviewSummary, SuggestionCategory
from src.plugins.builtin.code_reviewer.reviewing import summary_from


class Suggestion:
    def __init__(self, comment: str, category: SuggestionCategory) -> None:
        self.comment = comment
        self.category = category


WRITTEN = CodeReviewSummary(
    overview="Moves the plugins up so a worker has them before it claims work.",
    key_improvements=["The first job a worker takes is now gated like the rest"],
    minor_suggestions=["a suggestion that did not survive"],
    critical_issues=["a finding that did not survive"],
)


def test_what_was_written_about_the_change_survives_its_findings():
    kept = summary_from([], WRITTEN)

    assert kept.overview == WRITTEN.overview
    assert kept.key_improvements == WRITTEN.key_improvements


def test_only_the_findings_that_survived_are_carried():
    surviving = [
        Suggestion("a real bug", SuggestionCategory.BUG),
        Suggestion("a nicety", SuggestionCategory.STYLE),
    ]

    kept = summary_from(surviving, WRITTEN)

    assert kept.critical_issues == ["a real bug"]
    assert kept.minor_suggestions == ["a nicety"]


def test_with_nothing_written_it_still_says_what_it_found():
    kept = summary_from([Suggestion("a real bug", SuggestionCategory.BUG)])

    assert kept.overview == "Review found 1 actionable issue(s)."
