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


class TestBoundAboveTheLineItIsClaimedMissingOn:
    """Bound somewhere is not in scope here, so the line decides.

    A name bound above the hunk is one the review could not see. A name bound
    only inside an unrelated function, below or elsewhere, is genuinely absent
    where the review is looking, and saying so is right.
    """

    SOURCE = (
        "import os\n"
        "\n"
        "LIMIT = 6\n"
        "\n"
        "\n"
        "def run(work):\n"
        "    size = LIMIT\n"
        "    return work[:size]\n"
        "\n"
        "\n"
        "def other():\n"
        "    scratch = 1\n"
        "    return scratch\n"
    )

    def evidence(self):
        from src.core.review_evidence import CachedChangedFileEvidenceReader

        return CachedChangedFileEvidenceReader(lambda path: self.SOURCE).read("a.py")

    def decided(self, comment, at):
        return StructuralReviewEvidenceValidator().validate(
            list(claimed_absent(comment)), self.evidence(), at=at
        )

    def test_a_local_bound_above_contradicts_the_claim(self):
        assert self.decided("`size` is not defined here.", at=8).contradicted

    def test_the_same_name_claimed_above_its_binding_does_not(self):
        assert not self.decided("`size` is not defined here.", at=3).contradicted

    def test_a_parameter_counts_as_bound(self):
        assert self.decided("`work` is not defined.", at=8).contradicted

    def test_a_parameter_beats_a_later_assignment_of_the_same_name(self):
        """The line that matters is where the name arrives, not where it is
        next written to."""
        source = (
            "def outer(target):\n"
            "    return target\n"
            "\n"
            "\n"
            "def inner():\n"
            "    target = 1\n"
            "    return target\n"
        )
        from src.core.review_evidence import CachedChangedFileEvidenceReader

        evidence = CachedChangedFileEvidenceReader(lambda path: source).read("a.py")

        assert evidence.bindings["target"] == 1

    def test_a_name_the_file_never_binds_stands(self):
        assert not self.decided("`missing` is not defined.", at=8).contradicted

    def test_without_a_line_only_the_file_wide_facts_answer(self):
        """An older caller passes no line, and module scope still decides."""
        validator = StructuralReviewEvidenceValidator()

        assert validator.validate(
            list(claimed_absent("`LIMIT` is not defined.")), self.evidence()
        ).contradicted
        assert not validator.validate(
            list(claimed_absent("`scratch` is not defined.")), self.evidence()
        ).contradicted


class TestEveryWayPythonBindsAName:
    """A loop target, a context manager, a caught exception and a parameter
    each bind a name, and none of them is an assignment. Matched rather than
    parsed, all four were missed and the type inside an annotation was read
    as a name the file binds."""

    def bound(self, source):
        from src.core.review_evidence import CachedChangedFileEvidenceReader

        return (
            CachedChangedFileEvidenceReader(lambda path: source).read("a.py").bindings
        )

    def test_a_loop_target(self):
        assert "item" in self.bound("for item in items:\n    pass\n")

    def test_a_context_manager(self):
        assert "handle" in self.bound("with open('f') as handle:\n    pass\n")

    def test_a_caught_exception(self):
        assert "problem" in self.bound(
            "try:\n    pass\nexcept ValueError as problem:\n    pass\n"
        )

    def test_a_parameter_with_a_call_as_its_default(self):
        assert "limit" in self.bound("def run(limit=max(1, 2)):\n    return limit\n")

    def test_an_imported_name(self):
        assert "sleep" in self.bound("from time import sleep\n")

    def test_a_renamed_import(self):
        bound = self.bound("import numpy as np\n")

        assert "np" in bound
        assert "numpy" not in bound

    def test_a_type_inside_an_annotation_is_not_bound_here(self):
        """`str` is used in the annotation, not bound by it. Counted as bound,
        a true finding about it would be thrown away."""
        bound = self.bound("def typed(a: Union[int, str]):\n    return a\n")

        assert "a" in bound
        assert "str" not in bound

    def test_a_walrus_binds_too(self):
        assert "found" in self.bound("if (found := lookup()):\n    pass\n")

    def test_a_file_that_does_not_parse_binds_nothing(self):
        assert self.bound("def broken(:\n") == {}
