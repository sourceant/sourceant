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

    model: str
    input_tokens: int
    output_tokens: int
    #: What the provider says it cost, where it says so at all.
    cost: Optional[float] = None
    #: What asked, e.g. "review" or "initialization", for reading a bill back.
    purpose: str = ""
    workspace: Optional[str] = None
    repository: Optional[str] = None
    organization: Optional[str] = None
    user: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
