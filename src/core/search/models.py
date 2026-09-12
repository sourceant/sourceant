from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.scope import Scope

#: What a term may be: anything a reader could paste into a search box. A
#: name, a path, a snake_case identifier, a fragment of a line. Held to one
#: line because it is handed to a search as a single pattern, and to three
#: characters because shorter than that matches most files.
TERM = re.compile(r"[^\x00-\x1f\x7f]{3,128}")


@dataclass(frozen=True)
class SearchQuery:
    scope: Scope
    terms: tuple[str, ...]
    limit: int = 8

    def __post_init__(self):
        # Where to look, in one of three ways. A repository at a revision is
        # the change under review. A repository without one is a sibling the
        # change reaches, which the caller cannot pin to a revision because it
        # is not the one being reviewed; it is searched as it was last read. A
        # workspace is everything an account holds.
        if not self.scope.get("repository") and not self.scope.get("workspace"):
            raise ValueError(
                "Code text search needs a repository, with or without a "
                "revision, or a workspace"
            )
        if not 1 <= len(self.terms) <= 16 or any(
            not re.fullmatch(TERM, term) or term != term.strip() for term in self.terms
        ):
            raise ValueError("Supply between one and sixteen code search terms")
        if not 1 <= self.limit <= 20:
            raise ValueError("Code text search limit must be between one and twenty")


@dataclass(frozen=True)
class SearchMatch:
    path: str
    revision: str
    start_line: int
    end_line: int
    text: str
    #: Which repository it was found in. Empty where the search covered only
    #: one, so a path on its own is enough to find the file again.
    repository: str = ""


@dataclass(frozen=True)
class SearchResult:
    matches: tuple[SearchMatch, ...] = ()
    truncated: bool = False
    unavailable: str | None = None
