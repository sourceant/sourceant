"""What a token may reach, resolved the way the rest of the API means it."""

from sqlmodel import Session, SQLModel, create_engine

from src.mcp_server.auth import connected_repository_entitlement
from src.models.connected_repository import ConnectedRepository
from src.models.repository import Repository


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
        session.add(ConnectedRepository(user_id=7, repository_id=repository.id))
        session.commit()
    return engine


def test_a_connected_repository_is_reachable_and_names_its_provider(tmp_path):
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("7", "acme/shop") == "github"


def test_a_subject_written_as_a_pair_is_read_as_the_user(tmp_path):
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("user:7", "acme/shop") == "github"


def test_somebody_else_cannot_reach_it(tmp_path):
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("8", "acme/shop") is None


def test_a_repository_nobody_connected_is_not_reachable(tmp_path):
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("7", "acme/other") is None


def test_a_subject_that_is_not_a_user_cannot_borrow_that_number(tmp_path):
    """User 7 connected this repository. Installation 7 is a different thing."""
    entitled = connected_repository_entitlement(store(tmp_path))

    assert entitled("7", "acme/shop") == "github"
    assert entitled("installation:7", "acme/shop") is None
    assert entitled("workspace:7", "acme/shop") is None
    assert entitled("client:7", "acme/shop") is None


def test_without_a_database_nothing_is_reachable():
    entitled = connected_repository_entitlement(None)

    assert entitled("7", "acme/shop") is None
