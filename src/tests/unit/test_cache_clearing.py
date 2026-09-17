import pytest

from src.core.cache import CLEARABLE, Owner, keyed, owner
from src.core.cache.sql import SQLCache

ACME = Owner("repository", "acme/web")
OTHER = Owner("repository", "acme/other")


@pytest.fixture
def kept(tmp_path, monkeypatch):
    from sqlmodel import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    monkeypatch.setattr("src.core.cache.sql.get_engine", lambda: engine)
    return SQLCache(create_schema=True)


def test_clearing_one_scope_leaves_the_others(kept):
    kept.set("model-response", keyed("a"), "one", ttl=60, scope=ACME)
    kept.set("model-response", keyed("b"), "two", ttl=60, scope=OTHER)

    assert kept.clear("model-response", ACME) == 1

    assert kept.get("model-response", keyed("a")) is None
    assert kept.get("model-response", keyed("b")) == "two"


def test_clearing_a_namespace_takes_everything_under_it(kept):
    kept.set("model-response", keyed("a"), "one", ttl=60, scope=ACME)
    kept.set("model-response", keyed("b"), "two", ttl=60)
    kept.set("topology.reading", keyed("c"), "read", ttl=60, scope=ACME)

    assert kept.clear("model-response") == 2

    assert kept.get("topology.reading", keyed("c")) == "read"


def test_an_entry_nobody_said_who_it_was_for_survives_a_scoped_clear(kept):
    """Everything written before a scope was recorded has to keep answering."""
    kept.set("model-response", keyed("old"), "one", ttl=60)

    assert kept.clear("model-response", ACME) == 0
    assert kept.get("model-response", keyed("old")) == "one"


def test_what_belongs_to_a_call_is_the_narrowest_thing_it_names():
    assert owner({"workspace": "w1", "repository": "acme/web"}) == ACME
    assert owner({"workspace": "w1"}) == Owner("workspace", "w1")
    assert owner({}) is None


def test_every_clearable_namespace_is_one_something_writes():
    """A button for a namespace nothing writes clears nothing for ever."""
    import subprocess

    written = subprocess.run(
        [
            "grep",
            "-rhoE",
            r'"(model-response|topology\.reading|review\.[a-z]+)"',
            "src/core",
            "src/llms",
        ],
        capture_output=True,
        text=True,
    ).stdout
    for namespace in CLEARABLE:
        assert f'"{namespace}"' in written, namespace


def _asked(namespace="model-response", scope_type=None, scope_id=None):
    from src.api.routes.caches import Clearing

    return Clearing(namespace=namespace, scope_type=scope_type, scope_id=scope_id)


def test_a_token_cannot_clear_another_workspace(monkeypatch):
    from fastapi import HTTPException

    from src.api.routes import caches

    monkeypatch.setattr(caches, "_repositories", lambda workspace: [])

    with pytest.raises(HTTPException) as refused:
        caches._owners(_asked(scope_type="workspace", scope_id="99"), "7")

    assert refused.value.status_code == 403


def test_a_token_cannot_clear_a_repository_it_has_not_connected(monkeypatch):
    from fastapi import HTTPException

    from src.api.routes import caches

    monkeypatch.setattr(caches, "_repositories", lambda workspace: ["acme/web"])

    with pytest.raises(HTTPException) as refused:
        caches._owners(_asked(scope_type="repository", scope_id="acme/other"), "7")

    assert refused.value.status_code == 403


def test_asking_for_nothing_in_particular_stays_inside_the_workspace(monkeypatch):
    """No scope means everything this workspace has, not everything there is."""
    from src.api.routes import caches

    monkeypatch.setattr(caches, "_repositories", lambda workspace: ["acme/web"])

    owners = caches._owners(_asked(), "7")

    assert owners == [Owner("workspace", "7"), Owner("repository", "acme/web")]
    assert None not in owners


def test_the_repositories_a_workspace_connected_are_read_not_stubbed(
    tmp_path, monkeypatch
):
    """The query itself, because stubbing it hid a real unpacking error."""
    from sqlmodel import Session, SQLModel, create_engine

    from src.api.routes import caches
    from src.models.connected_repository import ConnectedRepository
    from src.models.repository import Repository
    from src.models.workspace import Workspace

    engine = create_engine(f"sqlite:///{tmp_path / 'repos.db'}")
    for model in (Repository, Workspace, ConnectedRepository):
        model.__table__.create(engine, checkfirst=True)
    monkeypatch.setattr(caches, "get_engine", lambda: engine)

    with Session(engine) as session:
        workspace = Workspace(external_ref="28")
        session.add(workspace)
        repository = Repository(
            provider="github",
            name="web",
            full_name="acme/web",
            url="https://github.com/acme/web",
            private=False,
            archived=False,
            visibility="public",
            owner="acme",
            owner_type="Organization",
            default_branch="main",
        )
        session.add(repository)
        session.commit()
        session.add(
            ConnectedRepository(workspace_id=workspace.id, repository_id=repository.id)
        )
        session.commit()

    assert caches._repositories("28") == ["acme/web"]
    assert caches._repositories("nobody") == []


def test_the_set_naming_what_a_scope_has_is_given_an_expiry():
    """EXPIRE GT leaves a key that has none alone, so it must not be used here."""
    from src.core.cache.redis import RedisCache

    calls = []

    class Client:
        def setex(self, *args):
            calls.append(("setex", args))

        def sadd(self, *args):
            calls.append(("sadd", args))

        def expire(self, *args, **kwargs):
            calls.append(("expire", args, kwargs))

    kept = RedisCache()
    kept._client = Client()
    kept.set("model-response", "k", "v", ttl=60, scope=ACME)

    expiry = [one for one in calls if one[0] == "expire"]
    assert expiry, "the membership set was never given an expiry"
    assert expiry[0][2] == {}, "GT never sets an expiry on a key without one"
