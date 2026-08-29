"""Whether a path is one a skill said it was about.

The skill format lets an author write globs saying which files their skill
applies to. That is a statement, not a guess, and it beats anything read out of
the wording, so it is worth matching properly.

`fnmatch` is not enough: it lets `*` cross a directory separator, so
`src/*.py` would match `src/deep/down/thing.py`, and it has no `**` at all.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

# A leading `**/` is written to mean "anywhere", including the top, so it has to
# match no directories at all as well as several.
ANYWHERE = "**/"


@lru_cache(maxsize=512)
def _compiled(glob: str) -> re.Pattern[str]:
    pattern = ["(?:^|/)" if glob.startswith(ANYWHERE) else "^"]
    if glob.startswith(ANYWHERE):
        glob = glob[len(ANYWHERE) :]

    index = 0
    while index < len(glob):
        character = glob[index]
        if character == "*":
            if glob[index : index + 3] == "**/":
                pattern.append("(?:.*/)?")
                index += 3
                continue
            if glob[index : index + 2] == "**":
                pattern.append(".*")
                index += 2
                continue
            # One star stops at a separator, which is the whole point of it.
            pattern.append("[^/]*")
        elif character == "?":
            pattern.append("[^/]")
        else:
            pattern.append(re.escape(character))
        index += 1

    pattern.append("$")
    return re.compile("".join(pattern))


def matches(path: str, globs: Iterable[str]) -> bool:
    """Whether a file is one of the ones a skill named."""
    tidied = (path or "").replace("\\", "/").lstrip("./")
    for glob in globs:
        glob = (glob or "").strip().replace("\\", "/")
        if not glob:
            continue
        # A bare directory means everything under it, which is how people write
        # it and not what the glob would otherwise say.
        if glob.endswith("/"):
            glob += "**"
        if _compiled(glob).search(tidied):
            return True
    return False


def any_match(paths: Iterable[str], globs: Iterable[str]) -> bool:
    globs = tuple(globs)
    if not globs:
        return False
    return any(matches(path, globs) for path in paths)
