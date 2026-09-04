"""The personal environment: one workspace, no token to read it from.

The workspace name is a constant. Anything derived from the computer would move
the settings when it is renamed.
"""

from __future__ import annotations

from typing import Optional

from src.core.environment import LOCAL
from src.llms.llm_interface import LLMInterface

from .model import ChosenModel


class LocalEnvironment:
    """One workspace, and the model it chose."""

    def __init__(self) -> None:
        self._model = ChosenModel()

    @property
    def name(self) -> str:
        return LOCAL

    def workspace_for(self, claims: Optional[dict] = None) -> str:
        """Always the one workspace, whatever the claims say."""
        return LOCAL

    def provider_for(self, workspace: str) -> Optional[LLMInterface]:
        return self._model.provider_for(user=workspace or LOCAL)
