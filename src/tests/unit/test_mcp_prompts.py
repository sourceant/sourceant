import asyncio

from mcp.server.auth.settings import AuthSettings

from src.core.mcp.auth import EntitledScopeResolver, SourceAntTokenVerifier
from src.core.mcp.surface import hosted_surface, personal_surface
from src.mcp_server.application import load_plugins
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
    return None


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
