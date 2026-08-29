"""Where recent change is landing on what the rest of the code leans on."""

import subprocess
from collections import Counter

from src.core.code_index.attention import attention, changes_by_file, weigh


def repository(root, commits):
    """A checkout with a history, because the history is half the answer."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    for name, value in (
        ("user.email", "nobody@example.com"),
        ("user.name", "Nobody"),
    ):
        subprocess.run(
            ["git", "config", name, value], cwd=root, check=True, capture_output=True
        )
    for number, touched in enumerate(commits):
        for path in touched:
            written = root / path
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text(f"# {number}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", f"Change {number}"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    return root


class TestHowOftenAFileChanges:
    def test_every_commit_that_touched_it_counts(self, tmp_path):
        root = repository(
            tmp_path / "billing",
            [["a.py"], ["a.py", "b.py"], ["a.py"]],
        )

        counted = changes_by_file(root)

        assert counted["a.py"] == 3
        assert counted["b.py"] == 1

    def test_somewhere_that_is_not_a_checkout_counts_nothing(self, tmp_path):
        assert changes_by_file(tmp_path) == Counter()


class TestWeighingTheTwoTogether:
    def test_something_nothing_leans_on_does_not_rise_on_churn_alone(self):
        # Somebody's scratch pad, rewritten every day.
        assert weigh(dependants=0, changes=50) == 0

    def test_something_nobody_has_touched_does_not_rise_on_position_alone(self):
        # Settled, and leaving it alone is correct.
        assert weigh(dependants=50, changes=0) == 0

    def test_both_together_outrank_either_alone(self):
        both = weigh(dependants=8, changes=8)

        assert both > weigh(dependants=40, changes=1)
        assert both > weigh(dependants=1, changes=40)


class TestWhatIsWorthLookingAt:
    def test_the_overlap_comes_first(self, tmp_path):
        root = repository(
            tmp_path / "billing",
            [
                ["core.py", "scratch.py"],
                ["core.py", "scratch.py"],
                ["core.py", "scratch.py"],
                ["settled.py"],
            ],
        )
        # core.py is imported widely; scratch.py by nobody; settled.py by
        # everybody but it has barely changed.
        dependants = {"core.py": 12, "scratch.py": 0, "settled.py": 30}

        found = attention(dependants, root)

        assert found[0].path == "core.py"
        assert "scratch.py" not in [item.path for item in found]

    def test_a_repository_with_no_recent_history_says_nothing(self, tmp_path):
        root = repository(tmp_path / "billing", [["a.py"]])

        # A window nothing can fall inside, which is what a repository nobody
        # has touched this quarter looks like.
        assert attention({"a.py": 3}, root, since="2099-01-01") == ()

    def test_what_it_answers_carries_both_numbers(self, tmp_path):
        root = repository(tmp_path / "billing", [["core.py"], ["core.py"]])

        found = attention({"core.py": 5}, root)

        assert found[0].changes == 2
        assert found[0].dependants == 5
