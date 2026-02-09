import difflib
from src.utils.diff_parser import parse_diff


def _make_diff(before_lines, after_lines, filename="test_file.py"):
    """Helper to create a unified diff string from before/after content."""
    diff_text = "".join(
        difflib.unified_diff(
            [l + "\n" for l in before_lines],
            [l + "\n" for l in after_lines],
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )
    return diff_text


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
