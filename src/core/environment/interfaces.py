"""Which deployment this is, and whose workspace a request acts in."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from src.llms.llm_interface import LLMInterface


@runtime_checkable
class Environment(Protocol):
    """Where SourceAnt is running, and who a request is acting as."""

    @property
    def name(self) -> str:
        """``local`` or ``hosted``."""
        ...

    def workspace_for(self, claims: Optional[dict] = None) -> str:
        """The workspace this request acts in.

        Hosted reads it off the token and refuses a request carrying none.
        Personal ignores the claims: there is only one workspace.
        """
        ...

    def model_for(self, workspace: str) -> Optional[LLMInterface]:
        """The model billed to that workspace, or None if none is chosen."""
        ...
