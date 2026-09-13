from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.core.settings.configuration import Configuration
from src.llms.llm_interface import LLMInterface


@runtime_checkable
class LLMSource(Protocol):
    """Which model to use, for the configuration being done.

    None means no model is named at any scope reaching this caller. Nothing
    that proposes or judges runs until one is.
    """

    def provider_for(self, configuration: Configuration) -> LLMInterface | None: ...

    def config_for(self, configuration: Configuration) -> Any: ...
