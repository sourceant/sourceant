"""The workspace a call is acting in.

Working it out in two places produced two answers: a call arriving without one
was a 400 from the API and an unhandled error from the plugin. One place, one
answer, and a workspace that things can point at.
"""

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from src.core.workspace import (
    connection_of,
    remember,
    repositories_of,
    workspace_in,
    workspace_of,
)
from src.models.connected_repository import ConnectedRepository
from src.models.repository import Repository
from src.models.workspace import Workspace


def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workspace.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def a_repository(session: Session, full_name: str) -> Repository:
    owner, name = full_name.split("/")
    repository = Repository(
        provider="github",
        name=name,
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        private=False,
        archived=False,
        visibility="public",
        owner=owner,
        owner_type="Organization",
        default_branch="main",
    )
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return repository


class TestTheWorkspaceOnTheCall:
    def test_it_is_read_from_the_scope_the_gateway_signed(self):
        assert workspace_of({"scope": {"workspace_id": "w42"}}) == "w42"

    def test_an_editor_token_is_answered_the_same_way(self):
        assert workspace_of({"workspace": "w42"}) == "w42"

    def test_a_number_is_answered_as_the_string_it_keys_on(self):
        assert workspace_of({"scope": {"workspace_id": 42}}) == "42"

    @pytest.mark.parametrize(
        "claim",
        [{}, {"scope": None}, {"scope": {}}, {"scope": {"workspace_id": ""}}],
    )
    def test_a_call_without_one_cannot_be_answered(self, claim):
        """Answered across an account instead, it would report on repositories
        the caller reached from a workspace they are no longer in."""
        with pytest.raises(HTTPException) as refused:
            workspace_of(claim)

        assert refused.value.status_code == 400


class TestWhereeverTheTokenPutsIt:
    """Two credentials reach this deployment, decoded with the same secret. The
    gateway signs a scope object; a token issued for an editor names the
    workspace at the top level. Reading only one of those refuses the other
    while looking straight at the answer."""

    def test_the_gateway_names_it_in_a_scope(self):
        assert workspace_in({"scope": {"workspace_id": "w42"}}) == "w42"

    def test_an_editor_token_names_it_at_the_top(self):
        assert workspace_in({"workspace": "w42"}) == "w42"

    def test_the_scope_wins_where_a_token_carries_both(self):
        assert (
            workspace_in({"scope": {"workspace_id": "w42"}, "workspace": "w99"})
            == "w42"
        )

    def test_a_token_naming_none_is_answered_with_none(self):
        assert workspace_in({}) is None
        assert workspace_in({"scope": {}}) is None

    def test_a_scope_that_is_not_an_object_is_not_read_into(self):
        assert workspace_in({"scope": "w42"}) is None


class TestRecordingThatAWorkspaceExists:
    def test_it_is_recorded_on_first_sight(self, tmp_path):
        with Session(store(tmp_path)) as session:
            remembered = remember(session, "w42")

            assert remembered.external_id == "w42"
            assert remembered.id is not None

    def test_seeing_it_again_records_nothing_further(self, tmp_path):
        with Session(store(tmp_path)) as session:
            first = remember(session, "w42")
            again = remember(session, "w42")

            assert first.id == again.id
            assert len(session.exec(select(Workspace)).all()) == 1

    def test_nothing_about_the_workspace_itself_is_copied(self):
        """A name kept here would be a second answer to a question the gateway
        already answers, and the two would disagree the first time one was
        renamed."""
        assert set(Workspace.model_fields) == {
            "id",
            "external_id",
            "created_at",
            "updated_at",
        }


class TestWhatAWorkspaceHasTakenOn:
    def test_it_answers_only_that_workspace(self, tmp_path):
        with Session(store(tmp_path)) as session:
            ours = a_repository(session, "acme/ours")
            theirs = a_repository(session, "acme/theirs")
            mine = remember(session, "w42")
            yours = remember(session, "w99")
            session.add(
                ConnectedRepository(workspace_id=mine.id, repository_id=ours.id)
            )
            session.add(
                ConnectedRepository(workspace_id=yours.id, repository_id=theirs.id)
            )
            session.commit()

            assert repositories_of(session, "w42") == [ours.id]

    def test_a_workspace_that_has_taken_on_nothing_reaches_nothing(self, tmp_path):
        with Session(store(tmp_path)) as session:
            assert repositories_of(session, "w42") == []

    def test_it_finds_one_workspace_hold_on_one_repository(self, tmp_path):
        with Session(store(tmp_path)) as session:
            ours = a_repository(session, "acme/ours")
            mine = remember(session, "w42")
            session.add(
                ConnectedRepository(workspace_id=mine.id, repository_id=ours.id)
            )
            session.commit()

            assert connection_of(session, "w42", ours.id) is not None
            assert connection_of(session, "w99", ours.id) is None

    def test_what_a_workspace_took_on_points_at_the_row(self, tmp_path):
        """Not at the name on the token. Only the row knows which name that is,
        so renaming one outside would not strand what belongs to it."""
        with Session(store(tmp_path)) as session:
            ours = a_repository(session, "acme/ours")
            mine = remember(session, "w42")
            session.add(
                ConnectedRepository(workspace_id=mine.id, repository_id=ours.id)
            )
            session.commit()

            held = session.exec(select(ConnectedRepository)).one()
            assert held.workspace_id == mine.id


class TestLeavingTheTransactionToTheCaller:
    def test_a_workspace_it_records_is_not_settled_on_its_own(self, tmp_path):
        """Recording one is half of connecting a repository to it. Committing
        here settles a workspace whose reason for existing has not been written
        yet, and leaves it behind if the rest fails."""
        engine = store(tmp_path)
        with Session(engine) as session:
            remember(session, "w42")
            session.rollback()

        with Session(engine) as session:
            assert session.exec(select(Workspace)).all() == []

    def test_it_still_hands_back_an_id_to_point_at(self, tmp_path):
        with Session(store(tmp_path)) as session:
            assert remember(session, "w42").id is not None

    def test_what_the_caller_commits_arrives_together(self, tmp_path):
        engine = store(tmp_path)
        with Session(engine) as session:
            repository_id = a_repository(session, "acme/ours").id
            session.add(
                ConnectedRepository(
                    workspace_id=remember(session, "w42").id,
                    repository_id=repository_id,
                )
            )
            session.commit()

        with Session(engine) as session:
            assert repositories_of(session, "w42") == [repository_id]
