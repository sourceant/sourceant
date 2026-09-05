"""A deployment that supplies its own source is asked both questions."""

import src.core.settings.resolver as resolver
from src.core.model import LLMSource, config_for, provider_for
from src.core.services import ServiceRegistry
from src.plugins.builtin.local.llm import ChosenLLM


def test_a_registered_source_decides_the_config_as_well_as_the_provider(monkeypatch):
    monkeypatch.setattr(resolver, "STATELESS_MODE", True)
    registry = ServiceRegistry()
    registry.register(LLMSource, ChosenLLM(), "local")

    assert provider_for(user="local", services=registry) is None
    assert config_for(user="local", services=registry) is None


def test_without_one_the_deployment_answers(monkeypatch):
    monkeypatch.setattr(resolver, "STATELESS_MODE", True)
    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-2.5-flash")
    registry = ServiceRegistry()

    named = config_for(user="local", services=registry)

    assert named is not None
    assert named.name
