"""The Codetown language as its library publishes it.

`vocabulary.json` is copied from github.com/sourceant/codetown at the commit
named in `VERSION`, so the words a town is written in are the library's, not
a second list kept here. Refresh both together.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import import_module
from pathlib import Path

VERSION = "ecbf12431f32ebafd8f59f838888ba33fcdad1cb"


@cache
def vocabulary() -> dict:
    path = Path(__file__).parent / "vocabulary.json"
    if path.is_file():
        return json.loads(path.read_text())
    # A compiled build ships data files inside the package, not beside it.
    resources = import_module("src._compiled_resources").resources()
    return json.loads(resources["core/review/codetown/vocabulary.json"])


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
