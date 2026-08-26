"""What a token may reach, resolved the way the rest of the API means it."""

from sqlmodel import Session, SQLModel, create_engine

from src.mcp_server.auth import connected_repository_entitlement
from src.models.connected_repository import ConnectedRepository
from src.models.repository import Repository
from src.models.workspace import Workspace


def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'entitlement.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        repository = Repository(
            provider="github",
            name="shop",
            full_name="acme/shop",
            url="https://github.com/acme/shop",
            private=False,
            archived=False,
            visibility="public",
            owner="acme",
            owner_type="Organization",
            default_branch="main",
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
        workspace = Workspace(external_id="7")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        session.add(
            ConnectedRepository(workspace_id=workspace.id, repository_id=repository.id)
        )
        session.commit()
    return engine


def test_a_repository_the_workspace_connected_is_reachable(tmp_path):
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("7", "acme/shop") == "github"


def test_another_workspace_cannot_reach_it(tmp_path):
    """The same person in two workspaces reaches two different sets, which is
    the whole reason connecting belongs to the workspace."""
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("8", "acme/shop") is None


def test_a_repository_nobody_connected_is_not_reachable(tmp_path):
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("7", "acme/other") is None


def test_a_token_naming_no_workspace_reaches_nothing(tmp_path):
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("", "acme/shop") is None


def test_without_a_database_nothing_is_reachable():
    entitled = connected_repository_entitlement(None)

    assert entitled("7", "acme/shop") is None
