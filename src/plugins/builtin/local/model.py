"""The model chosen on this computer, with no deployment fallback."""

from __future__ import annotations

from typing import Optional

from src.core.model import SettingsModelSource
from src.llms.llm_interface import LLMInterface

from src.core.environment import LOCAL


class ChosenModel(SettingsModelSource):
    """The chosen model, or None.

    Unlike a hosted deployment there is nothing to fall back to: the bill is
    the user's, so an unchosen model stays unchosen.
    """

    def __init__(self) -> None:
        super().__init__(fallback_model="")

    def provider_for(
        self,
        *,
        repository: Optional[str] = None,
        organization: Optional[str] = None,
        user: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> LLMInterface | None:
        return super().provider_for(
            repository=repository,
            organization=organization,
            user=user or LOCAL,
            workspace=workspace,
        )
