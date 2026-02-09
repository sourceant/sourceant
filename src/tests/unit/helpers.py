import difflib


def make_diff(before_lines, after_lines, filename="test_file.py"):
    """Create a unified diff string from before/after line lists."""
    return "".join(
        difflib.unified_diff(
            [line + "\n" for line in before_lines],
            [line + "\n" for line in after_lines],
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )
