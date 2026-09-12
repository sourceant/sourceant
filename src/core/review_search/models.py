"""What a review asked to look at, and what came back."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

#: How many times the model may come back for more before the review starts.
#: Each round is a call, and a reader waiting on a pull request notices.
MAX_ROUNDS = 3

#: How many searches it may run across all rounds.
MAX_SEARCHES = 8


@dataclass(frozen=True)
class Asked:
    """One search the model asked for."""

    repository: str
    terms: tuple[str, ...]
    #: What came back, or why nothing did.
    matches: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    unavailable: str = ""

    @property
    def answered(self) -> bool:
        return not self.unavailable
