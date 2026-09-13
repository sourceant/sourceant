"""Which model is used. Answered from settings unless a plugin registers its own."""

from src.core.services import ServiceRegistry, service_registry
from src.core.settings.configuration import Configuration
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
    configuration: Configuration,
    services: ServiceRegistry = service_registry,
) -> LLMConfig | None:
    """What to call and on whose account, for callers that do the call
    themselves. The provider is synchronous; an async caller needs the parts."""
    return llm_source(services).config_for(configuration)


def provider_for(
    configuration: Configuration,
    services: ServiceRegistry = service_registry,
) -> LLMInterface | None:
    """The model for this configuration, or None where no scope in it names one."""
    return llm_source(services).provider_for(configuration)


__all__ = [
    "LLMConfig",
    "config_for",
    "LLMSource",
    "SettingsLLMSource",
    "llm_source",
    "provider_for",
]
