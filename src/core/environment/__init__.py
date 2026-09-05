"""Which deployment this is, hosted or personal, and whose workspace is asking."""

from typing import Optional

from src.core.services import ServiceRegistry, service_registry

from .interfaces import Environment

LOCAL = "local"
HOSTED = "hosted"


def environment(services: ServiceRegistry = service_registry) -> Optional[Environment]:
    """Whatever registered as the environment, or None."""
    try:
        return services.resolve(Environment)
    except LookupError:
        return None


def workspace_here(
    claims: Optional[dict] = None, services: ServiceRegistry = service_registry
) -> str:
    """The workspace this request acts in. LOCAL where no environment registered."""
    deployment = environment(services)
    return deployment.workspace_for(claims) if deployment is not None else LOCAL


__all__ = [
    "HOSTED",
    "LOCAL",
    "Environment",
    "environment",
    "workspace_here",
]
