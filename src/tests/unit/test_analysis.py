"""What a review can know before it asks a model anything."""

from pathlib import Path

import pytest

from src.core.analysis import ERROR, NOTE, WARNING, Analysis, Analyzer, examine
from src.core.analysis.models import AnalyzerFinding
from src.core.analysis.semgrep import SemgrepAnalyzer
from src.core.services import ServiceRegistry


def _finding(**overrides) -> AnalyzerFinding:
    named = {
        "path": "src/app.py",
        "start_line": 12,
        "end_line": 12,
        "rule": "unused-import",
        "message": "os is imported and never used.",
        "severity": WARNING,
        "tool": "semgrep",
    }
    named.update(overrides)
    return AnalyzerFinding(**named)


class Found:
    """A tool that reports whatever it was built with."""

    name = "made-up"

    def __init__(self, *findings, available=True):
        self._findings = findings
        self._available = available

    def available(self) -> bool:
        return self._available

    def examine(self, root, paths):
        return self._findings


class Broken:
    name = "broken"

    def available(self) -> bool:
        return True

    def examine(self, root, paths):
        raise RuntimeError("it fell over")


@pytest.fixture
def services():
    return ServiceRegistry()


class TestWhatWasFound:
    def test_findings_from_every_tool_are_gathered(self, services, tmp_path):
        services.contribute(Analyzer, Found(_finding()), "one")
        services.contribute(Analyzer, Found(_finding(path="src/other.py")), "two")

        answered = examine(tmp_path, ["src/app.py"], services)

        assert len(answered.findings) == 2
        assert answered.ran == ("made-up", "made-up")
        assert answered.unavailable == ()

    def test_a_tool_that_is_not_installed_is_not_a_clean_change(
        self, services, tmp_path
    ):
        """The difference this records is the whole value of the answer."""
        services.contribute(Analyzer, Found(available=False), "one")

        answered = examine(tmp_path, ["src/app.py"], services)

        assert answered.findings == ()
        assert answered.ran == ()
        assert answered.unavailable == ("made-up",)

    def test_a_tool_that_falls_over_does_not_take_the_review_with_it(
        self, services, tmp_path
    ):
        services.contribute(Analyzer, Broken(), "one")
        services.contribute(Analyzer, Found(_finding()), "two")

        answered = examine(tmp_path, ["src/app.py"], services)

        assert len(answered.findings) == 1
        assert answered.unavailable == ("broken",)

    def test_nothing_contributed_falls_back_to_the_one_core_ships(self, services):
        from src.core.analysis import analyzers

        assert [type(one) for one in analyzers(services)] == [SemgrepAnalyzer]


class TestWhatTheReviewIsTold:
    def test_nothing_found_says_nothing(self):
        assert Analysis().rendered() is None

    def test_a_finding_is_rendered_with_where_and_what(self):
        told = Analysis(findings=(_finding(),)).rendered()

        assert "src/app.py:12" in told
        assert "os is imported and never used." in told
        assert "semgrep unused-import" in told

    def test_a_span_is_rendered_as_a_span(self):
        told = Analysis(findings=(_finding(start_line=4, end_line=9),)).rendered()

        assert "src/app.py:4-9" in told

    def test_the_worst_are_listed_first(self):
        told = Analysis(
            findings=(
                _finding(path="a.py", severity=NOTE),
                _finding(path="b.py", severity=ERROR),
            )
        ).rendered()

        assert told.index("b.py") < told.index("a.py")

    def test_a_change_with_too_much_wrong_is_not_listed_line_by_line(self):
        from src.core.analysis import MOST_REPORTED

        told = Analysis(
            findings=tuple(
                _finding(start_line=n, end_line=n) for n in range(MOST_REPORTED + 50)
            )
        ).rendered()

        assert told.count("\n- ") == MOST_REPORTED


class TestWhetherSomethingWasAlreadySaid:
    def test_the_same_line_is_covered(self):
        assert _finding().covers("src/app.py", 12, 12)

    def test_another_file_is_not(self):
        assert not _finding().covers("src/other.py", 12, 12)

    def test_a_line_or_two_away_is_covered(self):
        """A tool points at what it parsed, a reviewer at what it was thinking of."""
        assert _finding().covers("src/app.py", 14, 14)

    def test_far_enough_away_is_not(self):
        assert not _finding().covers("src/app.py", 40, 40)

    def test_an_overlapping_span_is_covered(self):
        assert _finding(start_line=10, end_line=20).covers("src/app.py", 18, 25)

    def test_counting_by_severity(self):
        answered = Analysis(
            findings=(
                _finding(severity=ERROR),
                _finding(severity=ERROR),
                _finding(severity=WARNING),
            )
        )

        assert (answered.counted(ERROR), answered.counted(WARNING)) == (2, 1)


class TestSemgrepItself:
    def test_a_missing_binary_is_reported_rather_than_run(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)

        assert SemgrepAnalyzer().available() is False

    def test_files_that_are_not_there_are_not_examined(self, tmp_path):
        assert SemgrepAnalyzer().examine(tmp_path, ["nothing.py"]) == ()

    def test_what_semgrep_answers_is_read_into_findings(self, tmp_path, monkeypatch):
        """Built from semgrep's documented output shape, not from its wording."""
        (tmp_path / "app.py").write_text("import os\n")
        answered = {
            "results": [
                {
                    "check_id": "python.lang.correctness.unused-import",
                    "path": "app.py",
                    "start": {"line": 1},
                    "end": {"line": 1},
                    "extra": {
                        "message": "os is imported\n  but never used.",
                        "severity": "ERROR",
                    },
                }
            ]
        }
        monkeypatch.setattr(SemgrepAnalyzer, "_run", lambda self, root, paths: answered)

        found = SemgrepAnalyzer().examine(Path(tmp_path), ["app.py"])

        assert len(found) == 1
        assert found[0].path == "app.py"
        assert found[0].start_line == 1
        assert found[0].rule == "unused-import"
        assert found[0].severity == ERROR
        assert found[0].message == "os is imported but never used."
        assert found[0].tool == "semgrep"

    def test_a_result_with_no_line_is_skipped_rather_than_guessed_at(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "app.py").write_text("import os\n")
        monkeypatch.setattr(
            SemgrepAnalyzer,
            "_run",
            lambda self, root, paths: {"results": [{"path": "app.py", "extra": {}}]},
        )

        assert SemgrepAnalyzer().examine(Path(tmp_path), ["app.py"]) == ()

    def test_a_run_that_could_not_answer_says_so(self, tmp_path, monkeypatch):
        """Answering with nothing would read as a change with nothing wrong."""
        from src.core.analysis.interfaces import AnalyzerFailed

        (tmp_path / "app.py").write_text("import os\n")

        def broken(self, root, paths):
            raise AnalyzerFailed("semgrep stopped with 2")

        monkeypatch.setattr(SemgrepAnalyzer, "_run", broken)

        with pytest.raises(AnalyzerFailed):
            SemgrepAnalyzer().examine(Path(tmp_path), ["app.py"])

    def test_a_tool_that_failed_is_recorded_as_unavailable(self, tmp_path, monkeypatch):
        from src.core.analysis.interfaces import AnalyzerFailed
        from src.core.services import ServiceRegistry

        (tmp_path / "app.py").write_text("import os\n")

        def broken(self, root, paths):
            raise AnalyzerFailed("semgrep stopped with 2")

        monkeypatch.setattr(SemgrepAnalyzer, "_run", broken)
        monkeypatch.setattr(SemgrepAnalyzer, "available", lambda self: True)
        registry = ServiceRegistry()
        registry.contribute(Analyzer, SemgrepAnalyzer(), "core")

        answered = examine(Path(tmp_path), ["app.py"], registry)

        assert answered.ran == ()
        assert answered.unavailable == ("semgrep",)


class TestWritingAChangeOutForATool:
    """The webhook path never clones, so the changed files are written out."""

    def test_the_files_are_written_under_their_own_paths(self):
        from src.core.analysis.checkout import written_out

        held = {"src/app.py": "import os\n", "docs/readme.md": "# hello\n"}
        with written_out(held, held.get) as (root, written):
            assert set(written) == set(held)
            assert (root / "src/app.py").read_text() == "import os\n"
            assert (root / "docs/readme.md").read_text() == "# hello\n"

    def test_nothing_survives_the_examination(self):
        from src.core.analysis.checkout import written_out

        with written_out(["a.py"], lambda path: "x\n") as (root, _):
            kept = root
        assert not kept.exists()

    def test_a_file_that_could_not_be_read_is_left_out_not_written_empty(self):
        """An empty file is a file a tool reports nothing about."""
        from src.core.analysis.checkout import written_out

        with written_out(["gone.py"], lambda path: None) as (root, written):
            assert written == ()
            assert not (root / "gone.py").exists()

    def test_a_read_that_raises_does_not_stop_the_others(self):
        from src.core.analysis.checkout import written_out

        def read(path):
            if path == "bad.py":
                raise RuntimeError("the forge said no")
            return "fine\n"

        with written_out(["bad.py", "good.py"], read) as (root, written):
            assert written == ("good.py",)

    def test_a_path_that_climbs_out_of_the_tree_is_refused(self):
        from src.core.analysis.checkout import written_out

        with written_out(["../escaped.py", "/etc/passwd"], lambda p: "x") as (
            root,
            written,
        ):
            assert written == ()


class TestWhatTheReviewIsLeftToSay:
    """A reviewer repeating a linter is the same opinion costing a comment."""

    @staticmethod
    def _said(path, start_line, end_line=None):
        from src.models.code_review import CodeSuggestion, Side, SuggestionCategory

        return CodeSuggestion(
            file_name=path,
            start_line=start_line,
            end_line=end_line if end_line is not None else start_line,
            side=Side.RIGHT,
            comment="Something the model noticed.",
            category=SuggestionCategory.BUG,
            suggested_code="a better line",
        )

    def _beyond(self, analysis, suggestions):
        from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer

        return CodeReviewer._beyond(analysis, suggestions)

    def test_a_finding_repeating_a_tool_on_the_same_lines_is_dropped(self):
        said = [self._said("src/app.py", 12)]
        said[0].comment = "os is imported here and never used."

        assert self._beyond(Analysis(findings=(_finding(),)), said) == []

    def test_a_finding_elsewhere_is_left_alone(self):
        """The part no tool was going to reach is the part worth keeping."""
        said = [self._said("src/app.py", 90)]

        assert self._beyond(Analysis(findings=(_finding(),)), said) == said

    def test_nothing_found_drops_nothing(self):
        said = [self._said("src/app.py", 12)]

        assert self._beyond(Analysis(), said) == said

    def test_no_analysis_at_all_drops_nothing(self):
        said = [self._said("src/app.py", 12)]

        assert self._beyond(None, said) == said

    def test_another_file_on_the_same_line_is_left_alone(self):
        said = [self._said("src/other.py", 12)]

        assert self._beyond(Analysis(findings=(_finding(),)), said) == said


def test_a_severity_no_tool_here_defines_does_not_stop_the_review():
    """A contributed tool reports whatever severity it likes."""
    told = Analysis(
        findings=(
            _finding(path="a.py", severity="catastrophic"),
            _finding(path="b.py", severity=ERROR),
        )
    ).rendered()

    assert told.index("b.py") < told.index("a.py")


class TestNotLosingAFindingToALine:
    """Two problems share a line often enough that overlap is not sameness."""

    @staticmethod
    def _said(comment, path="src/app.py", line=12):
        from src.models.code_review import CodeSuggestion, Side, SuggestionCategory

        return CodeSuggestion(
            file_name=path,
            start_line=line,
            end_line=line,
            side=Side.RIGHT,
            comment=comment,
            category=SuggestionCategory.BUG,
            suggested_code="a better line",
        )

    def _beyond(self, analysis, suggestions):
        from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer

        return CodeReviewer._beyond(analysis, suggestions)

    def test_the_same_complaint_reworded_is_dropped(self):
        said = [self._said("The os import is never used anywhere here.")]
        found = Analysis(findings=(_finding(message="os is imported and never used."),))

        assert self._beyond(found, said) == []

    def test_a_different_problem_on_the_same_line_is_kept(self):
        """The one that matters: this used to be discarded and never posted."""
        said = [self._said("This dereferences user before the None check above it.")]
        found = Analysis(findings=(_finding(message="os is imported and never used."),))

        assert self._beyond(found, said) == said

    def test_a_finding_on_a_line_no_tool_touched_is_kept(self):
        said = [self._said("Anything at all.", line=90)]
        found = Analysis(findings=(_finding(),))

        assert self._beyond(found, said) == said


def _a_review():
    from src.models.code_review import CodeReview, CodeReviewSummary, Verdict

    return CodeReview(
        verdict=Verdict.COMMENT,
        code_suggestions=[],
        summary=CodeReviewSummary(
            overview="An overview.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        ),
    )


class TestWhatTheToolFoundReachesTheReader:
    """A finding dropped for repeating a tool is lost unless the tool is heard."""

    @staticmethod
    def _reviewed(analysis):
        from src.core.analysis import also_reported

        return also_reported(_a_review(), analysis)

    def test_an_error_is_reported_as_a_critical_issue(self):
        reviewed = self._reviewed(Analysis(findings=(_finding(severity=ERROR),)))

        assert any(
            "os is imported and never used." in one
            for one in reviewed.summary.critical_issues
        )

    def test_anything_less_is_reported_as_a_minor_suggestion(self):
        reviewed = self._reviewed(Analysis(findings=(_finding(severity=WARNING),)))

        assert any(
            "os is imported and never used." in one
            for one in reviewed.summary.minor_suggestions
        )

    def test_nothing_found_adds_nothing(self):
        reviewed = self._reviewed(Analysis())

        assert reviewed.summary.critical_issues == []
        assert reviewed.summary.minor_suggestions == []


class TestSurvivingTheSummaryBeingRewritten:
    """A review's summary is written once and then replaced by another.

    Anything added to the first is thrown away, which is how the tool findings
    were reaching nobody while every unit test passed.
    """

    def test_findings_added_before_the_rewrite_are_lost(self):
        from src.core.analysis import also_reported
        from src.models.code_review import CodeReviewSummary

        review = _a_review()
        also_reported(review, Analysis(findings=(_finding(severity=ERROR),)))

        # What summarize_changes does: a new summary, not an edited one.
        review.summary = CodeReviewSummary(
            overview="The whole change.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )

        assert review.summary.critical_issues == []

    def test_reporting_after_the_rewrite_reaches_the_reader(self):
        from src.core.analysis import also_reported
        from src.models.code_review import CodeReviewSummary

        review = _a_review()
        review.summary = CodeReviewSummary(
            overview="The whole change.",
            key_improvements=[],
            minor_suggestions=[],
            critical_issues=[],
        )
        also_reported(review, Analysis(findings=(_finding(severity=ERROR),)))

        assert any(
            "os is imported and never used." in one
            for one in review.summary.critical_issues
        )

    def test_reporting_twice_does_not_say_it_twice(self):
        from src.core.analysis import also_reported

        review = _a_review()
        found = Analysis(findings=(_finding(severity=ERROR),))
        also_reported(review, found)
        also_reported(review, found)

        assert len(review.summary.critical_issues) == 1


class TestOnlyTalkingAboutTheChange:
    """A tool reads whole files; most of what it reports was already there."""

    def _filtered(self, findings, touched):
        from src.core.analysis import about_the_change

        return about_the_change(Analysis(findings=findings, ran=("semgrep",)), touched)

    def test_a_finding_on_a_line_the_change_wrote_is_kept(self):
        found = (_finding(start_line=12, end_line=12),)

        assert self._filtered(found, {"src/app.py": {12}}).findings == found

    def test_a_finding_on_a_line_the_change_did_not_touch_is_dropped(self):
        """This is most of what a default rule pack reports on a real repo."""
        found = (_finding(start_line=107, end_line=109),)

        assert self._filtered(found, {"src/app.py": {12, 13}}).findings == ()

    def test_a_span_overlapping_one_written_line_is_kept(self):
        found = (_finding(start_line=10, end_line=20),)

        assert self._filtered(found, {"src/app.py": {15}}).findings == found

    def test_a_file_the_change_did_not_touch_is_dropped(self):
        found = (_finding(path="untouched.py"),)

        assert self._filtered(found, {"src/app.py": {12}}).findings == ()

    def test_what_ran_is_still_recorded_when_everything_is_dropped(self):
        """A tool that ran and found only old problems still ran."""
        filtered = self._filtered((_finding(start_line=900, end_line=900),), {})

        assert filtered.findings == ()
        assert filtered.ran == ("semgrep",)


def test_which_lines_a_change_wrote_are_read_from_the_diff():
    from src.core.analysis import touched_lines
    from src.utils.diff_parser import parse_diff
    from src.tests.unit.helpers import make_diff

    parsed = parse_diff(
        make_diff(["def run():", "    return 1"], ["def run():", "    return 2"])
    )
    written = touched_lines(parsed)

    assert written
    assert all(isinstance(lines, set) for lines in written.values())
    assert all(isinstance(line, int) for lines in written.values() for line in lines)
