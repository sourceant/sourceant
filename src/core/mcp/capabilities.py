"""What an MCP token may do, beyond reaching the server at all."""

from __future__ import annotations

from functools import wraps
from typing import Callable, TypeVar

from mcp.server.auth.middleware.auth_context import get_access_token

READ = "read"
WRITE_KNOWLEDGE = "write:knowledge"
WRITE_REQUIREMENTS = "write:requirements"
WRITE_SYSTEMS = "write:systems"
WRITE_GROUPS = "write:groups"
CAPABILITIES = frozenset(
    {READ, WRITE_KNOWLEDGE, WRITE_REQUIREMENTS, WRITE_SYSTEMS, WRITE_GROUPS}
)

Tool = TypeVar("Tool", bound=Callable)


def require(capability: str) -> None:
    """Refuse the call unless the token carries the capability.

    A token naming no capability predates them and keeps full access, and a
    server reached without a token (a local one over stdio) checks nothing.
    """
    token = get_access_token()
    if token is None:
        return
    held = CAPABILITIES.intersection(token.scopes)
    if held and capability not in held:
        raise PermissionError(f"This token does not carry the {capability} scope")


def scoped(capability: str) -> Callable[[Tool], Tool]:
    def wrap(tool: Tool) -> Tool:
        @wraps(tool)
        def checked(*args, **kwargs):
            require(capability)
            return tool(*args, **kwargs)

        return checked  # type: ignore[return-value]

    return wrap
