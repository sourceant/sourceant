"""Which files are worth somebody's attention, and why.

Two things are known about every file here without asking anybody. How much of
the repository leans on it, which the import graph says. And how often it has
been changing, which git says.

Neither is interesting alone. A file half the codebase imports and nobody has
touched in two years is settled, and leaving it alone is correct. A file that
changes constantly and nothing imports is somebody's scratch pad. It is the
overlap that matters: the defect research is consistent that faults cluster
where frequent change meets a central position in the dependency graph, and
that is also, for anybody new, the shortest list of files worth reading first.

What this does not do is guess at quality. Nothing here has an opinion about
whether a file is good; it says where change lands and what leans on it, and
leaves the judgement to whoever reads it.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 60

# Long enough to see a pattern rather than last week's sprint, short enough
# that a rewrite two years ago is not still being reported as turbulence.
SINCE = "90 days ago"

# Beyond this a git log is measuring the repository rather than the recent
# past, and the answer stops changing anyway.
MAX_COMMITS = 2_000


@dataclass(frozen=True)
class Attention:
    path: str
    # How many other files import this one, directly.
    dependants: int
    # How many commits in the window touched it.
    changes: int
    # Both together, as a rank rather than a score anybody should read as one.
    weight: float


def changes_by_file(root: Path, since: str = SINCE) -> Counter[str]:
    """How many recent commits touched each file.

    Merges are left out: a merge commit touches everything it brought in and
    would report a quiet file as the busiest in the repository.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "log",
                "--no-merges",
                f"--since={since}",
                f"--max-count={MAX_COMMITS}",
                "--name-only",
                "--pretty=format:",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Counter()
    if completed.returncode != 0:
        return Counter()
    return Counter(
        line.strip() for line in completed.stdout.splitlines() if line.strip()
    )


def weigh(dependants: int, changes: int) -> float:
    """One number from the two, for ordering and nothing else.

    Multiplied rather than added, so a file has to be both to rise: something
    everything imports and nobody touches stays where it is, and so does
    somebody's scratch pad. The logarithms stop one enormous count deciding
    the whole list on its own.
    """
    from math import log1p

    return log1p(dependants) * log1p(changes)


def attention(
    dependants: dict[str, int], root: Path, limit: int = 10, since: str = SINCE
) -> tuple[Attention, ...]:
    """The files where change is landing on what the rest of the code leans on."""
    changed = changes_by_file(root, since)
    if not changed:
        return ()

    weighed = [
        Attention(
            path=path,
            dependants=dependants.get(path, 0),
            changes=count,
            weight=weigh(dependants.get(path, 0), count),
        )
        for path, count in changed.items()
    ]
    weighed = [item for item in weighed if item.weight > 0]
    weighed.sort(key=lambda item: (-item.weight, item.path))
    return tuple(weighed[:limit])
