"""A checkout for a tool to read, where the review has only a diff.

The webhook path never clones. It works from the diff text plus whatever it can
read a file at a time over the forge's API, and a static analyzer needs neither
of those: it needs files on a disk. So the changed files are written out as
they stand after the change, under the paths they have in the repository, and
the tool is pointed at that.

What this cannot do is worth saying plainly. Only the changed files are there,
so a rule that resolves a symbol into a file the change did not touch will not
find it. Most rules read one file's syntax and are unaffected; the ones that do
not will under-report rather than report wrongly.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Sequence

from src.utils.logger import logger


@contextmanager
def written_out(paths: Iterable[str], read_content: Callable[[str], str | None]):
    """The changed files on a disk, cleaned up afterwards.

    Yields the root and the paths that could actually be written. A file that
    could not be read is left out rather than written empty, because an empty
    file is a file a tool will happily report nothing about.
    """
    with tempfile.TemporaryDirectory(prefix="sourceant-analysis-") as home:
        root = Path(home)
        yield root, tuple(_written(root, paths, read_content))


def _written(
    root: Path, paths: Iterable[str], read_content: Callable[[str], str | None]
) -> Sequence[str]:
    for path in paths:
        if not path or Path(path).is_absolute() or ".." in Path(path).parts:
            continue
        try:
            content = read_content(path)
        except Exception:
            logger.warning("Could not read %s to examine it", path, exc_info=True)
            continue
        # A forge answers with whatever it has. Anything that is not text is
        # not something a tool can examine.
        if not isinstance(content, str):
            continue
        here = root / path
        try:
            here.parent.mkdir(parents=True, exist_ok=True)
            here.write_text(content, encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Could not write %s out to examine it", path, exc_info=True)
            continue
        yield path
