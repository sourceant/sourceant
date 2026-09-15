"""A deployment that supplies its own source is asked both questions."""

import src.core.settings.resolver as resolver
from src.core.model import LLMSource, config_for, provider_for
from src.core.settings.configuration import Configuration
from src.core.services import ServiceRegistry
from src.plugins.builtin.local.llm import ChosenLLM


def test_a_registered_source_decides_the_config_as_well_as_the_provider(monkeypatch):
    monkeypatch.setattr(resolver, "STATELESS_MODE", True)
    registry = ServiceRegistry()
    registry.register(LLMSource, ChosenLLM(), "local")

    assert provider_for(Configuration(user="local"), services=registry) is None
    assert config_for(Configuration(user="local"), services=registry) is None


def test_without_one_the_deployment_answers(monkeypatch):
    monkeypatch.setattr(resolver, "STATELESS_MODE", True)
    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-2.5-flash")
    registry = ServiceRegistry()

    named = config_for(Configuration(user="local"), services=registry)

    assert named is not None
    assert named.name


class _Chose:
    """Settings answering from what a scope was given, and nothing else."""

    def __init__(self, **values):
        self.values = values

    def value(self, key):
        return self.values.get(key)

    def with_workspace(self):
        return self


def test_a_key_given_without_a_model_still_reaches_the_provider():
    """Dropped, the reading fails for want of a credential that was set.

    A deployment naming a model it has no environment key for is the ordinary
    case for an install whose customers bring their own.
    """
    from src.core.model import SettingsLLMSource

    source = SettingsLLMSource(fallback_model="gemini/gemini-2.0-flash")

    chosen = source.config_for(_Chose(**{"model.api_key": "a-key"}))

    assert chosen.name == "gemini/gemini-2.0-flash"
    assert chosen.api_key == "a-key"


def test_an_endpoint_given_without_a_model_still_reaches_the_provider():
    from src.core.model import SettingsLLMSource

    source = SettingsLLMSource(fallback_model="gemini/gemini-2.0-flash")

    chosen = source.config_for(_Chose(**{"model.base_url": "https://proxy"}))

    assert chosen.base_url == "https://proxy"


def test_nothing_chosen_leaves_the_credentials_to_the_environment():
    """A deployment paying for its own calls sets neither, and litellm reads
    what it was started with."""
    from src.core.model import SettingsLLMSource

    source = SettingsLLMSource(fallback_model="gemini/gemini-2.0-flash")

    chosen = source.config_for(_Chose())

    assert chosen.api_key == ""
    assert chosen.base_url == ""
    assert chosen.credentials() == {}
