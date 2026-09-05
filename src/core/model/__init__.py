"""Which model gets asked. Answered from settings unless a plugin registers its own."""

from typing import Optional

from src.core.services import ServiceRegistry, service_registry
from src.core.workspace import workspace_holding
from src.llms.llm_interface import LLMInterface

from .interfaces import LLMSource
from .settings import LLMConfig, SettingsLLMSource

_core = SettingsLLMSource()


def llm_source(services: ServiceRegistry = service_registry) -> LLMSource:
    """Whatever registered as a source, else core's settings-backed one."""
    try:
        return services.resolve(LLMSource)
    except LookupError:
        return _core


def config_for(
    *,
    repository: Optional[str] = None,
    organization: Optional[str] = None,
    user: Optional[str] = None,
    workspace: Optional[str] = None,
) -> LLMConfig | None:
    """What to call and on whose account, for callers that do the call
    themselves. The provider is synchronous; an async caller needs the parts."""
    holder = workspace or (workspace_holding(repository) if repository else None)
    return _core.config_for(
        repository=repository,
        organization=organization,
        user=user,
        workspace=holder,
    )


def provider_for(
    *,
    repository: Optional[str] = None,
    organization: Optional[str] = None,
    user: Optional[str] = None,
    workspace: Optional[str] = None,
    services: ServiceRegistry = service_registry,
) -> LLMInterface | None:
    """The model to ask for this repository, workspace, organisation or user."""
    return llm_source(services).provider_for(
        repository=repository,
        organization=organization,
        user=user,
        workspace=workspace,
    )


__all__ = [
    "LLMConfig",
    "config_for",
    "LLMSource",
    "SettingsLLMSource",
    "llm_source",
    "provider_for",
]
