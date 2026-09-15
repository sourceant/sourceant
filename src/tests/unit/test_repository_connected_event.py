"""Connecting a repository says so, so that reading it need not be asked for.

Core queues nothing itself. What reading a repository means belongs to whatever
does the reading, and this is how it hears that there is something to read.
"""

import pytest

from src.api.routes.repos import CONNECTED, connected
from src.models.repository import Repository


def _repository() -> Repository:
    return Repository(
        id=135, provider="github", name="lens", full_name="sourceant/lens"
    )


def _user() -> dict:
    return {"user_id": "7", "github_token": "gh-token"}


@pytest.mark.asyncio
async def test_a_connection_says_what_was_connected_and_for_whom(monkeypatch):
    said = {}

    async def _record(event_type, event_data, **kwargs):
        said["type"] = event_type
        said["data"] = event_data
        return {}

    monkeypatch.setattr("src.core.plugins.event_hooks.broadcast_event", _record)

    await connected(_repository(), "workspace-2", _user())

    assert said["type"] == CONNECTED
    assert said["data"]["repository"] == "sourceant/lens"
    assert said["data"]["repository_id"] == 135
    assert said["data"]["workspace"] == "workspace-2"
    assert said["data"]["owner_id"] == "7"


@pytest.mark.asyncio
async def test_the_token_goes_with_it(monkeypatch):
    """A private repository cannot be read without one, and the person who
    granted it is here and nowhere later."""
    said = {}

    async def _record(event_type, event_data, **kwargs):
        said.update(event_data)
        return {}

    monkeypatch.setattr("src.core.plugins.event_hooks.broadcast_event", _record)

    await connected(_repository(), "workspace-2", _user())

    assert said["github_token"] == "gh-token"


@pytest.mark.asyncio
async def test_a_subscriber_that_breaks_does_not_refuse_the_connection(monkeypatch):
    """The repository is connected either way, and reading it can be asked for
    again by hand."""

    async def _breaks(event_type, event_data, **kwargs):
        raise RuntimeError("the subscriber fell over")

    monkeypatch.setattr("src.core.plugins.event_hooks.broadcast_event", _breaks)

    await connected(_repository(), "workspace-2", _user())
