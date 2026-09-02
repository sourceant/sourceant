"""The personal-computer plugin stays out of a hosted deployment.

It answers for the model as well as the environment, and its answer is that
nobody chose one, since on a personal machine the bill is the user's. Hosted,
that discards the model the deployment was started with and leaves it unable
to review anything.
"""

import importlib

from src.config import settings


def _metadata():
    module = importlib.import_module("src.plugins.builtin.local.plugin")
    return module.LocalPlugin({}).metadata


def test_it_is_off_where_the_deployment_is_hosted(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_MODE", False)

    assert _metadata().enabled is False


def test_it_is_on_where_somebody_is_running_it_themselves(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_MODE", True)

    assert _metadata().enabled is True


def test_a_hosted_deployment_still_resolves_the_model_it_was_started_with(
    monkeypatch,
):
    """With the plugin off, nothing registers a source, so core's own answers."""
    from src.core.model import model_source
    from src.core.model.settings import SettingsModelSource
    from src.core.services import ServiceRegistry

    monkeypatch.setattr(settings, "LOCAL_MODE", False)
    empty = ServiceRegistry()

    source = model_source(empty)

    assert isinstance(source, SettingsModelSource)
    assert source.fallback_model  # the deployment's own, not empty
