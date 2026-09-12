"""Absence asserted in words, checked against the file.

A review shown part of a file says a name is not defined. Sometimes it is,
hundreds of lines above the hunk, and the review has reported a failure that
cannot happen. The check for that existed and only ran on claims the review
declared, which is never the ones it is most sure of.
"""

import pytest

from src.core.review_evidence import (
    FileEvidence,
    ReviewClaim,
    StructuralFact,
    StructuralPredicate,
    StructuralReviewEvidenceValidator,
    claimed_absent,
)

DEFINED = StructuralPredicate.DEFINED
IMPORTED = StructuralPredicate.IMPORTED


def defining(*names):
    return FileEvidence(
        path="a.py",
        language="python",
        facts=frozenset(StructuralFact(name, DEFINED) for name in names),
        supported_predicates=frozenset({DEFINED, IMPORTED}),
    )


class TestWhatCountsAsClaimingAbsence:
    @pytest.mark.parametrize(
        "comment",
        [
            "The `MAX_AT_ONCE` constant is not defined within this file.",
            "`MAX_AT_ONCE` is undefined here.",
            "`MAX_AT_ONCE` needs to be defined at the module level.",
            "`MAX_AT_ONCE` must be defined before use.",
            "`MAX_AT_ONCE` has not been defined anywhere.",
        ],
    )
    def test_the_name_it_says_is_missing(self, comment):
        assert [one.subject for one in claimed_absent(comment)] == ["MAX_AT_ONCE"]

    def test_importing_is_its_own_claim(self):
        claimed = claimed_absent("`ThreadPoolExecutor` is not imported.")

        assert claimed[0].predicate is IMPORTED
        assert claimed[0].expected is False

    def test_the_nearest_name_before_the_assertion_is_the_subject(self):
        """In "x is used but y is not defined" the missing name is y."""
        claimed = claimed_absent("`pool` is used here but `limit` is not defined.")

        assert [one.subject for one in claimed] == ["limit"]

    @pytest.mark.parametrize(
        "comment",
        [
            "Consider renaming `handler` for clarity.",
            "`handler` is defined twice, which is confusing.",
            "This could be simpler.",
            "",
        ],
    )
    def test_a_comment_asserting_nothing_missing_claims_nothing(self, comment):
        assert claimed_absent(comment) == ()

    def test_prose_without_backticks_is_not_read_as_code(self):
        assert claimed_absent("The limit is not defined anywhere obvious.") == ()


class TestWhatTheFileSaysBack:
    def test_a_name_the_file_defines_contradicts_the_claim(self):
        comment = "`MAX_AT_ONCE` is used here but is not defined within this file."

        decision = StructuralReviewEvidenceValidator().validate(
            list(claimed_absent(comment)), defining("MAX_AT_ONCE")
        )

        assert decision.contradicted

    def test_a_name_the_file_really_lacks_stands(self):
        comment = "`MISSING_LIMIT` is not defined within this file."

        decision = StructuralReviewEvidenceValidator().validate(
            list(claimed_absent(comment)), defining("MAX_AT_ONCE")
        )

        assert not decision.contradicted

    def test_what_the_review_declared_is_still_checked(self):
        declared = [
            ReviewClaim(subject="MAX_AT_ONCE", predicate=DEFINED, expected=False)
        ]

        decision = StructuralReviewEvidenceValidator().validate(
            declared, defining("MAX_AT_ONCE")
        )

        assert decision.contradicted


class TestAReviewThatSaysItThroughTheReviewer:
    """The whole point, driven where a suggestion is actually filtered."""

    def test_a_suggestion_claiming_a_defined_name_is_missing_is_dropped(self):
        from src.core.review_evidence import CachedChangedFileEvidenceReader
        from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer
        from src.models.code_review import CodeSuggestion, Side, SuggestionCategory
        from src.utils.line_mapper import LineMapper
        from src.utils.diff_parser import parse_diff
        from src.utils.suggestion_filter import SuggestionFilter

        source = "LIMIT = 6\n\n\ndef run(work):\n    return work[:LIMIT]\n"
        diff = (
            "--- a/pool.py\n+++ b/pool.py\n@@ -1,5 +1,5 @@\n"
            " LIMIT = 6\n \n \n def run(work):\n"
            "-    return work\n+    return work[:LIMIT]\n"
        )
        parsed = parse_diff(diff)
        suggestion = CodeSuggestion(
            file_name="pool.py",
            start_line=5,
            end_line=5,
            side=Side.RIGHT,
            category=SuggestionCategory.BUG,
            comment=(
                "`LIMIT` is used here but is not defined within this file. "
                "This will lead to a `NameError`."
            ),
            existing_code="    return work[:LIMIT]",
            suggested_code="    return work[:6]",
        )

        kept = CodeReviewer().process(
            [suggestion],
            SuggestionFilter(),
            LineMapper(parsed),
            evidence=CachedChangedFileEvidenceReader(lambda path: source),
        )

        assert kept == []
