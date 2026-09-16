"""Closing or merging a pull request stops a review of it."""

import pytest

from src.core.review.stopping import abandon, abandoned


@pytest.fixture(autouse=True)
def _forget():
    yield
    from src.core.cache import cache, keyed
    from src.core.review.stopping import CURRENT, NAMESPACE

    for number in (7, 8):
        for space in (NAMESPACE, CURRENT):
            cache().forget(space, keyed("acme/web", str(number)))


def test_a_pull_request_nobody_abandoned_is_still_wanted():
    assert not abandoned("acme/web", 7)


def test_abandoning_one_is_seen_by_whatever_asks_next():
    abandon("acme/web", 7)

    assert abandoned("acme/web", 7)


def test_abandoning_one_leaves_the_others_alone():
    abandon("acme/web", 7)

    assert not abandoned("acme/web", 8)
    assert not abandoned("acme/other", 7)


@pytest.mark.parametrize("repository,number", [("", 7), ("acme/web", 0)])
def test_something_that_names_no_pull_request_is_not_abandoned(repository, number):
    abandon(repository, number)

    assert not abandoned(repository, number)


def test_a_cache_that_cannot_be_read_leaves_the_review_running(monkeypatch):
    class Broken:
        def get(self, *args, **kwargs):
            raise RuntimeError("no cache here")

        def set(self, *args, **kwargs):
            raise RuntimeError("no cache here")

    monkeypatch.setattr("src.core.review.stopping.cache", lambda: Broken())

    abandon("acme/web", 7)

    assert not abandoned("acme/web", 7)


def _changes():
    from src.core.change_context.models import ChangedFile, ChangeSet
    from src.core.scope import Scope

    return ChangeSet(
        scope=Scope.from_mapping({"repository": "acme/web"}),
        files=(ChangedFile(path="a.py"),),
        diff=(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -0,0 +1 @@\n+print('hi')\n"
        ),
    )


def _recording_model(asked):
    """A model that records what it was asked and produces nothing."""

    class Model:
        def count_tokens(self, text):
            return len(text)

        def generate_code_review(self, **kwargs):
            asked.append(kwargs)
            return None

    return Model


def test_an_abandoned_pull_request_is_not_read():
    from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer

    asked = []
    Model = _recording_model(asked)

    abandon("acme/web", 7)

    answer = CodeReviewer().review(_changes(), provider=Model(), metadata={"number": 7})

    assert answer is None
    assert asked == []


def test_stopping_abandons_the_pull_request(monkeypatch):
    from src.plugins.builtin.code_reviewer.plugin import stop_reviewing

    monkeypatch.setattr(
        "src.plugins.builtin.code_reviewer.plugin._drop_queued",
        lambda repository, number: None,
    )

    stop_reviewing("acme/web", 7)

    assert abandoned("acme/web", 7)


def test_a_queue_that_cannot_be_read_still_stops_the_running_one(monkeypatch):
    from src.plugins.builtin.code_reviewer.plugin import stop_reviewing

    monkeypatch.setattr(
        "src.core.jobs.job_store",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no queue")),
    )

    stop_reviewing("acme/web", 7)

    assert abandoned("acme/web", 7)


@pytest.mark.parametrize("key", ["review.stop_when_closed", "review.stop_on_new_push"])
def test_the_setting_is_on_unless_somebody_turns_it_off(key):
    from src.core.settings.definitions import (
        BY_KEY,
        ORGANIZATION,
        REPOSITORY,
        WORKSPACE,
    )

    setting = BY_KEY[key]

    assert setting.default is True
    assert setting.scopes == (REPOSITORY, WORKSPACE, ORGANIZATION)


def test_the_reviewer_is_told_when_a_pull_request_closes():
    from src.plugins.builtin.code_reviewer import plugin

    source = open(plugin.__file__).read()

    assert '"pull_request.closed"' in source


async def _closing(plugin, monkeypatch, merged: bool, stop: bool = True):
    from src.core.settings.configuration import Configuration

    reviewed = []
    monkeypatch.setattr(
        Configuration,
        "value",
        lambda self, key: stop if key == "review.stop_when_closed" else True,
    )
    monkeypatch.setattr(
        plugin, "generate_review", lambda *a, **k: reviewed.append(1) or {}
    )
    said = await plugin._handle_event(
        "pull_request.closed",
        {
            "auth_type": "github_app",
            "repository_event": {"number": 8, "title": "A change"},
            "repository_context": {
                "name": "web",
                "owner": "acme",
                "full_name": "acme/web",
            },
            "payload": {"pull_request": {"draft": False, "merged": merged}},
        },
    )
    return said, reviewed


@pytest.mark.asyncio
@pytest.mark.parametrize("merged", [True, False])
async def test_a_closed_pull_request_is_not_reviewed(merged, monkeypatch):
    from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

    said, reviewed = await _closing(CodeReviewerPlugin(), monkeypatch, merged)

    assert said["processed"] is False
    assert reviewed == []
    assert abandoned("acme/web", 8)


@pytest.mark.asyncio
async def test_the_setting_off_leaves_the_review_running(monkeypatch):
    from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

    said, reviewed = await _closing(
        CodeReviewerPlugin(), monkeypatch, merged=True, stop=False
    )

    assert said["processed"] is False
    assert reviewed == []
    assert not abandoned("acme/web", 8)


def test_a_review_of_the_current_revision_is_wanted():
    from src.core.review.stopping import supersede

    supersede("acme/web", 7, "abc123")

    assert not abandoned("acme/web", 7, "abc123")


def test_a_review_of_a_replaced_revision_is_not():
    from src.core.review.stopping import supersede

    supersede("acme/web", 7, "def456")

    assert abandoned("acme/web", 7, "abc123")


def test_a_review_that_names_no_revision_is_left_alone():
    from src.core.review.stopping import supersede

    supersede("acme/web", 7, "def456")

    assert not abandoned("acme/web", 7)


def test_closing_stops_a_review_of_the_current_revision_too():
    from src.core.review.stopping import supersede

    supersede("acme/web", 7, "abc123")
    abandon("acme/web", 7)

    assert abandoned("acme/web", 7, "abc123")


def test_a_replaced_revision_is_not_read():
    from src.core.review.stopping import supersede
    from src.plugins.builtin.code_reviewer.reviewing import CodeReviewer

    asked = []
    Model = _recording_model(asked)

    supersede("acme/web", 7, "def456")

    answer = CodeReviewer().review(
        _changes(), provider=Model(), metadata={"number": 7}, revision="abc123"
    )

    assert answer is None
    assert asked == []


@pytest.mark.asyncio
async def test_turning_the_push_setting_off_does_not_abandon_a_review(monkeypatch):
    """A revision recorded while the setting was on must not outlive it."""
    from src.core.review.stopping import supersede
    from src.core.settings.configuration import Configuration
    from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

    supersede("acme/web", 8, "older")

    asked = {}
    monkeypatch.setattr(
        Configuration,
        "value",
        lambda self, key: False if key == "review.stop_on_new_push" else True,
    )
    plugin = CodeReviewerPlugin()
    monkeypatch.setattr(
        plugin, "generate_review", lambda *a, **k: asked.update(k) or {}
    )

    await plugin._handle_event(
        "pull_request.synchronize",
        {
            "auth_type": "github_app",
            "repository_event": {"number": 8, "title": "A change"},
            "repository_context": {
                "name": "web",
                "owner": "acme",
                "full_name": "acme/web",
            },
            "payload": {
                "pull_request": {
                    "draft": False,
                    "merged": False,
                    "head": {"sha": "newer"},
                }
            },
        },
    )

    assert asked.get("revision") == ""


def test_a_reopened_pull_request_is_reviewed_again():
    from src.core.review.stopping import resume

    abandon("acme/web", 7)
    resume("acme/web", 7)

    assert not abandoned("acme/web", 7)


def test_resuming_one_leaves_a_replaced_revision_replaced():
    """Reopening says the pull request is wanted, not that a stale revision is."""
    from src.core.review.stopping import resume, supersede

    supersede("acme/web", 7, "newer")
    abandon("acme/web", 7)
    resume("acme/web", 7)

    assert not abandoned("acme/web", 7)
    assert abandoned("acme/web", 7, "older")


@pytest.mark.asyncio
async def test_reopening_clears_the_mark(monkeypatch):
    from src.core.settings.configuration import Configuration
    from src.plugins.builtin.code_reviewer.plugin import CodeReviewerPlugin

    abandon("acme/web", 8)
    monkeypatch.setattr(Configuration, "value", lambda self, key: True)
    plugin = CodeReviewerPlugin()
    monkeypatch.setattr(plugin, "generate_review", lambda *a, **k: {})

    await plugin._handle_event(
        "pull_request.reopened",
        {
            "auth_type": "github_app",
            "repository_event": {"number": 8, "title": "A change"},
            "repository_context": {
                "name": "web",
                "owner": "acme",
                "full_name": "acme/web",
            },
            "payload": {"pull_request": {"draft": False, "merged": False}},
        },
    )

    assert not abandoned("acme/web", 8)
