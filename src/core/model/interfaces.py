from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from src.core.settings.resolver import UNSTATED
from src.llms.llm_interface import LLMInterface


@runtime_checkable
class LLMSource(Protocol):
    """Which model to ask, at the scope asking.

    None means no model is named at any scope reaching this caller. Nothing
    that proposes or judges runs until one is.
    """

    def provider_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> LLMInterface | None: ...

    def config_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
        workspace: Any = UNSTATED,
    ) -> Any: ...
