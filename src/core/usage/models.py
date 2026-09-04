"""What one model call consumed, and on whose behalf."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelUsage:
    """One call to a model, as the provider reported it.

    Tokens come from the provider rather than from counting the prompt, because
    a count taken here sees neither the system prompt nor the answer, and it is
    the answer that costs the most.
    """

    #: Whose service answered, apart from which of their models did.
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    #: What the provider called the total. Some report only this, and a total
    #: with no split is still a real cost.
    reported_total: int = 0
    #: Millionths of a currency unit. Money is kept whole, because a bill is
    #: read as a sum of many rows and a float drifts as they add up.
    cost_micro: Optional[int] = None
    currency: str = "USD"
    #: What asked, e.g. "review" or "initialization", for reading a bill back.
    purpose: str = ""
    workspace: Optional[str] = None
    repository: Optional[str] = None
    organization: Optional[str] = None
    user: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.reported_total or (self.input_tokens + self.output_tokens)

    @staticmethod
    def micro(cost: Optional[float]) -> Optional[int]:
        """A provider's decimal cost as whole millionths."""
        return None if cost is None else round(cost * 1_000_000)
