"""Which model gets asked. Answered from settings unless a plugin registers its own."""

from typing import Optional

from src.core.services import ServiceRegistry, service_registry
from src.llms.llm_interface import LLMInterface

from .interfaces import ModelSource
from .settings import Choice, SettingsModelSource

_core = SettingsModelSource()


def model_source(services: ServiceRegistry = service_registry) -> ModelSource:
    """Whatever registered as a source, else core's settings-backed one."""
    try:
        return services.resolve(ModelSource)
    except LookupError:
        return _core


def model_for(
    *,
    repository: Optional[str] = None,
    organization: Optional[str] = None,
    user: Optional[str] = None,
    services: ServiceRegistry = service_registry,
) -> LLMInterface | None:
    """The model to ask for this repository, organisation or user."""
    return model_source(services).model_for(
        repository=repository, organization=organization, user=user
    )


__all__ = [
    "Choice",
    "ModelSource",
    "SettingsModelSource",
    "model_for",
    "model_source",
]
