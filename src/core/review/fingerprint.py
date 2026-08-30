"""Naming one finding so the same one is recognised in a later run.

Not by position: an edit above a finding moves its line, and a moved identity
orphans whatever state was set on it. By what was said and what was proposed,
hashed separately and matched either-or, since prose is reworded more often
than code.

Versioned, so changing the scheme starts a new generation rather than matching
old keys against a new rule.
"""

from __future__ import annotations

import hashlib
import re

VERSION = "1"

# A model varies whitespace and case while saying the same thing.
_SPACE = re.compile(r"\s+")


def _digest(*parts: str) -> str:
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def _flattened(text: str) -> str:
    return _SPACE.sub(" ", (text or "").strip().lower())


def of_words(path: str, comment: str) -> str:
    """What was said about a file, however it was spaced or capitalised."""
    return f"{VERSION}:w:{_digest(path or '', _flattened(comment))}"


def of_code(path: str, suggested: str) -> str:
    """What was proposed for a file, whatever prose came with it."""
    return f"{VERSION}:c:{_digest(path or '', _flattened(suggested))}"


def prints_for(path: str, comment: str, suggested: str) -> tuple[str, ...]:
    """Every name one finding answers to, the first being what it is filed under.

    The rest are looked up before filing a new one, so a suggestion whose
    wording changed but whose code did not is still the same finding.
    """
    names = [of_words(path, comment)]
    if (suggested or "").strip():
        names.append(of_code(path, suggested))
    return tuple(names)
