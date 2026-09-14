"""What one model call consumed, and on whose behalf."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class TokenUsage:
    """One call to a model, as the provider reported it.

    Tokens come from the provider rather than from counting the prompt, because
    a count taken here sees neither the system prompt nor the answer, and it is
    the answer that costs the most.
    """

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    #: Input tokens the provider served from its cache rather than reading again.
    cached_input_tokens: int = 0
    #: Input tokens the provider wrote into its cache for a later call to read.
    cache_write_tokens: int = 0
    reported_total: int = 0
    cost_micro: Optional[int] = None
    currency: str = "USD"
    purpose: str = ""
    owner_type: Optional[str] = None
    owner_id: Optional[str] = None
    #: What the call was about, where that is not the owner itself.
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.reported_total or (self.input_tokens + self.output_tokens)

    @property
    def uncached_input_tokens(self) -> int:
        """Input the provider had to read in full.

        Every provider litellm routes to counts cached reads and cache writes
        inside its prompt total, so what a cache saved is the difference rather
        than a separate number. Anthropic reports the two halves apart and
        litellm adds them back in before anything here sees them.
        """
        return max(
            0, self.input_tokens - self.cached_input_tokens - self.cache_write_tokens
        )

    @staticmethod
    def micro(cost: Optional[float]) -> Optional[int]:
        """A provider's decimal cost as whole millionths."""
        return None if cost is None else round(cost * 1_000_000)

    @staticmethod
    def owed_by(**scopes: Optional[str]) -> tuple:
        """Who owes for a call, and what it was about, from the scopes given.

        A workspace pays where one is known. Until one reaches here, whatever
        names the work stands in for it, and the repository becomes the subject
        as soon as something broader owns the call.
        """
        order = ("workspace", "organization", "user", "repository")
        named = [(kind, scopes.get(kind)) for kind in order if scopes.get(kind)]
        if not named:
            return (None, None, None, None)
        owner_type, owner_id = named[0]
        repository = scopes.get("repository")
        if repository and owner_type != "repository":
            return (owner_type, owner_id, "repository", repository)
        return (owner_type, owner_id, None, None)


@dataclass(frozen=True)
class UsageQuery:
    owner_type: str
    owner_id: str
    since: datetime
    repository: str = ""
    organization: str = ""
