from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.llms.llm_interface import LLMInterface


@runtime_checkable
class ModelSource(Protocol):
    """Which model to ask, at the scope asking.

    None means no model is named at any scope reaching this caller. Nothing
    that proposes or judges runs until one is.
    """

    def model_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
    ) -> LLMInterface | None: ...
