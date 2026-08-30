"""A change too large to read at once is read in parts.

The split is governed by how much can be read, not how much fits: a large
context window accepts a whole change and answers about part of it.
"""

from src.plugins.builtin.code_reviewer.reviewing import (
    DEFAULT_READING_BUDGET,
    _batched,
)


class Patch:
    def __init__(self, path: str, text: str) -> None:
        self.file_path = path
        self.diff_text = text
        self.is_binary_file = False


def size(text: str) -> int:
    return len(text)


def test_a_change_that_fits_is_one_part():
    files = [Patch("a.py", "x" * 10), Patch("b.py", "y" * 10)]

    assert len(_batched(files, 100, size)) == 1


def test_a_change_too_large_is_split_at_the_budget():
    files = [Patch(f"{n}.py", "x" * 40) for n in range(10)]

    batches = _batched(files, 100, size)

    assert len(batches) == 5
    assert all(sum(size(one.diff_text) for one in batch) <= 100 for batch in batches)


def test_every_file_is_read_exactly_once():
    files = [Patch(f"{n}.py", "x" * 30) for n in range(11)]

    batches = _batched(files, 100, size)

    read = [one.file_path for batch in batches for one in batch]
    assert read == [one.file_path for one in files]


def test_a_file_larger_than_the_whole_budget_is_read_alone():
    """Rather than dropped: a very large file is often the one worth reading."""
    files = [Patch("small.py", "x" * 10), Patch("huge.py", "y" * 500)]

    batches = _batched(files, 100, size)

    assert ["huge.py"] in [[one.file_path for one in batch] for batch in batches]


def test_order_is_kept_so_a_directory_travels_together():
    files = [
        Patch("src/api/one.py", "x" * 60),
        Patch("src/api/two.py", "x" * 60),
        Patch("src/core/three.py", "x" * 60),
    ]

    batches = _batched(files, 130, size)

    assert [one.file_path for one in batches[0]] == ["src/api/one.py", "src/api/two.py"]


def test_the_budget_is_far_below_a_model_context_window():
    """The number is about attention, not capacity."""
    assert DEFAULT_READING_BUDGET < 100_000
