"""The MCP server: transports, authentication, and the tools core serves.

Anything else a deployment can do reaches MCP by contributing a ToolProvider,
so a tool lives with the code that answers it rather than here.
"""

from src.core.services import ServiceRegistry, service_registry

from .interfaces import ToolProvider
from .server import create_mcp_server
from .surface import Surface, hosted_surface, personal_surface


def contribute_tools(
    provider: ToolProvider, name: str, services: ServiceRegistry = service_registry
) -> None:
    """Add one provider's tools to every server this deployment builds."""
    services.contribute(ToolProvider, provider, name)


__all__ = [
    "Surface",
    "ToolProvider",
    "contribute_tools",
    "create_mcp_server",
    "hosted_surface",
    "personal_surface",
]
