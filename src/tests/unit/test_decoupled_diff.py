import re

from src.tests.unit.helpers import make_diff as _make_diff
from src.utils.diff_parser import parse_diff


class TestDecoupledDiffFormat:
    def test_single_add(self):
        before = ["def hello():", '    print("hello")']
        after = ["def hello():", '    print("hello")', '    print("world")']
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 1

        result = parsed[0].to_decoupled_format()
        assert "## File:" in result
        assert "__new hunk__" in result
        assert '+    print("world")' in result

    def test_single_remove(self):
        before = ["def hello():", '    print("hello")', '    print("world")']
        after = ["def hello():", '    print("hello")']
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 1

        result = parsed[0].to_decoupled_format()
        assert "__old hunk__" in result
        assert '-    print("world")' in result

    def test_add_and_remove(self):
        before = ["a = 1", "b = 2", "c = 3"]
        after = ["a = 1", "b = 20", "c = 3"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 1

        result = parsed[0].to_decoupled_format()
        assert "__old hunk__" in result
        assert "__new hunk__" in result
        assert "-b = 2" in result
        assert "+b = 20" in result

    def test_multi_hunk(self):
        before = [f"line {i}" for i in range(20)]
        after = list(before)
        after[2] = "CHANGED line 2"
        after[17] = "CHANGED line 17"
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 1

        result = parsed[0].to_decoupled_format()
        assert result.count("__old hunk__") >= 1
        assert result.count("__new hunk__") >= 1
        assert "-line 2" in result
        assert "+CHANGED line 2" in result

    def test_empty_diff(self):
        before = ["a = 1"]
        after = ["a = 1"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 0

    def test_context_lines_present(self):
        before = ["a = 1", "b = 2", "c = 3", "d = 4"]
        after = ["a = 1", "b = 2", "c = 30", "d = 4"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 1

        result = parsed[0].to_decoupled_format()
        assert " a = 1" in result or " b = 2" in result

    def test_hunk_headers_present(self):
        before = ["a = 1", "b = 2"]
        after = ["a = 1", "b = 20"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)

        result = parsed[0].to_decoupled_format()
        assert "@@" in result

    def test_changed_line_count(self):
        before = ["a = 1", "b = 2", "c = 3"]
        after = ["a = 1", "b = 20", "c = 30"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 1
        # 2 removed + 2 added = 4 commentable lines
        assert parsed[0].changed_line_count == 4

    def test_new_hunk_lines_have_line_numbers(self):
        before = ["a = 1", "b = 2", "c = 3"]
        after = ["a = 1", "b = 20", "c = 3"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)
        assert len(parsed) == 1

        result = parsed[0].to_decoupled_format()
        new_hunk_start = result.index("__new hunk__")
        new_hunk_section = result[new_hunk_start:]

        assert re.search(r"\d+  a = 1", new_hunk_section) or re.search(
            r"\d+  c = 3", new_hunk_section
        )
        assert re.search(r"\d+ \+b = 20", new_hunk_section)

    def test_old_hunk_lines_no_line_numbers(self):
        before = ["a = 1", "b = 2", "c = 3"]
        after = ["a = 1", "b = 20", "c = 3"]
        diff_text = _make_diff(before, after)
        parsed = parse_diff(diff_text)

        result = parsed[0].to_decoupled_format()
        old_hunk_start = result.index("__old hunk__")
        new_hunk_start = result.index("__new hunk__")
        old_hunk_section = result[old_hunk_start:new_hunk_start]

        for line in old_hunk_section.splitlines():
            if line.startswith("-") or line.startswith(" "):
                assert not re.match(r"^\d+", line)
