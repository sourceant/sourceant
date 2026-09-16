import asyncio

import pytest
from mcp.server.auth.settings import AuthSettings

from src.core.mcp.auth import EntitledScopeResolver, SourceAntTokenVerifier
from src.core.mcp.surface import hosted_surface, personal_surface
from src.mcp_server.application import _finish, load_plugins
from src.plugins.builtin.code_reviewer.prompts import ReviewPrompts


class Recorder:
    def __init__(self):
        self.names = []

    def prompt(self, *, name, title="", description=""):
        self.names.append(name)

        def keep(fn):
            return fn

        return keep


def _offered(surface):
    server = Recorder()
    ReviewPrompts().add_tools(server, surface)
    return server.names


def test_a_checkout_is_offered_every_prompt():
    assert _offered(personal_surface()) == ["context", "remember", "review"]


def test_a_hosted_server_offers_the_ones_that_need_no_checkout():
    surface = hosted_surface(
        auth=AuthSettings(
            issuer_url="https://issuer.example.com",
            resource_server_url="https://sourceant.example.com/mcp",
            required_scopes=["sourceant"],
        ),
        token_verifier=SourceAntTokenVerifier(
            issuer="https://issuer.example.com",
            audience="sourceant",
            required_scopes=frozenset({"sourceant"}),
        ),
        scope_resolver=EntitledScopeResolver(lambda workspace, repository: None),
    )

    assert _offered(surface) == ["context", "remember"]


def test_a_surface_nobody_named_is_treated_as_a_checkout():
    assert _offered(None) == ["context", "remember", "review"]


def _watched(monkeypatch):
    """Record that initializing actually ran, rather than that nothing raised.

    load_plugins reports a failure as a warning and returns, so a test that
    only calls it passes whether or not the plugins were loaded.
    """
    from src.core.plugins import plugin_manager

    done = []

    async def initialize():
        done.append(True)

    monkeypatch.setattr(plugin_manager, "add_plugin_directory", lambda path: None)
    monkeypatch.setattr(plugin_manager, "load_all_plugins", lambda: _nothing())
    monkeypatch.setattr(plugin_manager, "initialize_plugins", initialize)
    return done


async def _nothing() -> None:
    pass


def test_plugins_load_from_a_caller_that_is_already_running_a_loop(monkeypatch):
    done = _watched(monkeypatch)

    async def inside() -> None:
        load_plugins()

    asyncio.run(inside())

    assert done == [True]


def test_plugins_load_from_a_caller_with_no_loop(monkeypatch):
    done = _watched(monkeypatch)

    load_plugins()

    assert done == [True]


def test_a_directory_is_searched_once_and_only_if_it_is_there(tmp_path):
    from src.core.plugins.plugin_manager import PluginManager
    from src.core.plugins.plugin_registry import PluginRegistry

    manager = PluginManager(registry=PluginRegistry())
    (tmp_path / "inside").mkdir()

    manager.add_plugin_directory(tmp_path)
    manager.add_plugin_directory(tmp_path / "inside" / "..")
    manager.add_plugin_directory(tmp_path / "nothing-here")

    assert manager._plugin_directories == [tmp_path.resolve()]


def test_loading_a_plugin_already_registered_keeps_the_one_registered():
    """Loading is reached twice, and the second must not report a failure."""
    import asyncio

    from src.core.plugins.plugin_manager import PluginManager
    from src.core.plugins.plugin_registry import PluginRegistry
    from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

    manager = PluginManager(registry=PluginRegistry())
    first = CodeReviewerPlugin(config={})
    manager.registry.register(first)

    loaded = []

    class Entry:
        def load(self):
            loaded.append(True)
            return CodeReviewerPlugin

    again = asyncio.run(
        manager._load_entrypoint_plugin("whatever-it-is-called", Entry())
    )

    assert again is first
    assert loaded == [True], "the entry point is read; the registry decides"


def test_the_file_loader_also_keeps_the_one_already_registered():
    """The other load path, which asks under a different name of its own."""
    import asyncio
    from pathlib import Path

    import src.plugins.builtin.code_reviewer.plugin as source
    from src.core.plugins.plugin_manager import PluginManager
    from src.core.plugins.plugin_registry import PluginRegistry
    from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

    manager = PluginManager(registry=PluginRegistry())
    first = CodeReviewerPlugin(config={})
    manager.registry.register(first)

    again = asyncio.run(
        manager.load_plugin(Path(source.__file__), "code_reviewer_reloaded")
    )

    assert again is first
    assert len(manager.registry.get_all_plugins()) == 1


def test_work_that_never_ran_is_closed_rather_than_left_waiting(monkeypatch):
    async def work() -> None:
        pass

    unstarted = work()

    def refuse(*args, **kwargs):
        raise RuntimeError("no loop to run this on")

    monkeypatch.setattr(asyncio, "run", refuse)

    with pytest.raises(RuntimeError):
        _finish(unstarted)

    assert unstarted.cr_frame is None
