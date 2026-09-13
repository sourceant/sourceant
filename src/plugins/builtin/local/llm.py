"""The model chosen on this computer, with no deployment fallback."""

from __future__ import annotations

from src.core.model import SettingsLLMSource
from src.core.settings.configuration import Configuration
from src.llms.llm_interface import LLMInterface

from src.core.environment import LOCAL


class ChosenLLM(SettingsLLMSource):
    """The chosen model, or None.

    Unlike a hosted deployment there is nothing to fall back to: the bill is
    the user's, so an unchosen model stays unchosen.
    """

    def __init__(self) -> None:
        super().__init__(fallback_model="")

    def provider_for(self, configuration: Configuration) -> LLMInterface | None:
        return super().provider_for(self._local(configuration))

    def config_for(self, configuration: Configuration):
        return super().config_for(self._local(configuration))

    @staticmethod
    def _local(configuration: Configuration) -> Configuration:
        """One machine, one person, so an unnamed user is this one."""
        return configuration.with_user(LOCAL)
