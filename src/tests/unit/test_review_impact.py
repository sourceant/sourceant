from pathlib import Path

from src.core.review.impact import impact, is_test
from src.utils.diff_parser import parse_diff

FIXTURES = Path(__file__).parent.parent / "fixtures/review-overview"


def _new_side(diff):
    """Each file as the change leaves it, for files the change creates."""
    files = {}
    for one in parse_diff(diff):
        lines = one.diff_text.splitlines()
        if any(line.startswith("--- /dev/null") for line in lines):
            files[one.file_path] = "\n".join(
                line[1:] for line in lines if line[:1] == "+" and line[:3] != "+++"
            )
    return files


def test_impact_names_what_the_change_defines_and_who_calls_it():
    diff = (FIXTURES / "full.diff").read_text()
    contents = _new_side(diff)

    measured = impact(diff, "sourceant/sourceant", contents.get)

    assert [one["path"] for one in measured["files"]] == [
        one.file_path for one in parse_diff(diff)
    ]
    suggestions = next(
        one
        for one in measured["files"]
        if one["path"] == "src/core/topology/suggestions.py"
    )
    assert (suggestions["additions"], suggestions["deletions"]) == (84, 0)
    root = next(one for one in measured["definitions"] if one["name"] == "root")
    assert [(one["name"], one["basis"]) for one in root["callers"]] == [
        ("suggest_groups", "matched by name")
    ]
    assert measured["consumers"] == []
    assert measured["tests_touched"] == []
    assert "src/core/topology" in measured["folders"]


def test_tests_are_known_by_common_naming_across_languages():
    assert all(
        is_test(path)
        for path in (
            "src/tests/unit/test_review_impact.py",
            "web/src/refunds/approve.test.ts",
            "app/models/user_spec.rb",
            "pkg/cache/cache_test.go",
            "packages/ui/__tests__/Button.tsx",
            "spec/requests/refunds_spec.rb",
        )
    )
    assert not any(
        is_test(path)
        for path in (
            "src/core/review/impact.py",
            "docs/testing-guide.md",
            "src/contest.py",
        )
    )
