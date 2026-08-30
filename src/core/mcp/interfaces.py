from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ToolProvider(Protocol):
    """Adds tools to an MCP server, so a tool lives with the code answering it."""

    @property
    def name(self) -> str:
        """Named in logs and in the tool listing that reports what was added."""
        ...

    def add_tools(self, server, surface) -> None:
        """Register this provider's tools, or none where the surface cannot serve them."""
        ...
