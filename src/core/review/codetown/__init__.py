"""The Codetown language as its library publishes it.

`vocabulary.json` is copied from github.com/sourceant/codetown at the commit
named in `VERSION`, so the words a town is written in are the library's, not
a second list kept here. Refresh both together.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

VERSION = "ecbf12431f32ebafd8f59f838888ba33fcdad1cb"


@cache
def vocabulary() -> dict:
    return json.loads((Path(__file__).parent / "vocabulary.json").read_text())


def word(keyword: str) -> str:
    """A keyword the library defines, or an error naming the one it does not."""
    known = {one["keyword"] for one in vocabulary()["concepts"]} | set(
        vocabulary()["statements"]
    )
    if keyword not in known:
        raise KeyError(f"Codetown has no word {keyword!r} at {VERSION[:7]}")
    return keyword


def allowed(name: str) -> list[str]:
    """The closed set an option takes, such as impacts or certainties."""
    return list(vocabulary()[name])
