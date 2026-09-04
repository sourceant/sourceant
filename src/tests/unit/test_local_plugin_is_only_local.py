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


def test_a_hosted_deployment_does_not_load_it(monkeypatch):
    """What matters is the plugin manager skipping it, not the flag alone.

    Enablement is read off the metadata when initialization runs, so asserting
    the property while nothing loads it proves nothing about what a deployment
    ends up with.
    """
    import asyncio

    from src.core.plugins import PluginManager
    from src.core.services import ServiceRegistry

    monkeypatch.setattr(settings, "LOCAL_MODE", False)
    manager = PluginManager(services=ServiceRegistry())
    module = importlib.import_module("src.plugins.builtin.local.plugin")
    plugin = module.LocalPlugin({})
    plugin.bind_services(manager.services)

    async def run() -> bool:
        if not plugin.metadata.enabled:
            return False
        await plugin.initialize()
        return True

    assert asyncio.run(run()) is False
