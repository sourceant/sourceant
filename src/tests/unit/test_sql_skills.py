from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select

from src.core.skills import Skill, SkillWriteError
from src.core.skills.sql import SQLSkillLibrary, skill_table, application_table


def test_database_skills_persist_with_scope_precedence_and_deletion(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
    store = SQLSkillLibrary(engine, create_schema=True)
    skill = Skill("retry", "Retries", "Review retries", "Bound retries.")
    store.write("one", skill, scope="workspace")
    changed = Skill("retry", "Retries", "Review retries", "Stop after three attempts.")
    store.write("one", changed, scope="repository", repository="acme/app")
    other = SQLSkillLibrary(engine)
    assert other.one("one", "retry").body == skill.body
    assert other.one("one", "retry", "acme/app").body == changed.body
    assert other.one("two", "retry", "acme/app") is None
    assert not other.forget("two", "retry", scope="workspace")
    before_deletion = datetime.now(timezone.utc)
    assert other.forget("one", "retry", scope="repository", repository="acme/app")
    after_deletion = datetime.now(timezone.utc)
    assert other.one("one", "retry", "acme/app").body == skill.body
    store.write("one", changed, scope="workspace")
    with engine.connect() as connection:
        rows = connection.execute(select(skill_table)).mappings().all()
        assert len(rows) == 2
        active = next(row for row in rows if row["deleted_at"] is None)
        removed = next(row for row in rows if row["deleted_at"] is not None)
        assert "deleted" not in active
        assert (
            before_deletion
            <= removed["deleted_at"].replace(tzinfo=timezone.utc)
            <= after_deletion
        )
        assert active["name"] == changed.name
        assert active["description"] == changed.description
        assert active["content"] == {"instructions": changed.body}
        assert active["scope"] == "workspace"
        assert active["automatic"] is True
        assert "document" not in active
    assert other.one("one", "retry").body == changed.body
    assert other.forget("one", "retry", scope="workspace")
    assert other.all("one") == ()
    store.write("one", skill, scope="workspace")
    assert other.one("one", "retry").body == skill.body
    with engine.connect() as connection:
        assert (
            connection.execute(
                select(skill_table.c.deleted_at).where(
                    skill_table.c.scope == "workspace"
                )
            ).scalar_one()
            is None
        )
    with pytest.raises(SkillWriteError):
        store.write("", skill, scope="workspace")
    with pytest.raises(SkillWriteError):
        store.write("one", skill, scope="system")


def test_application_rules_are_extensible_and_preserve_legacy_review_settings(tmp_path):
    from dataclasses import replace

    engine = create_engine(f"sqlite:///{tmp_path / 'applications.db'}")
    store = SQLSkillLibrary(engine, create_schema=True)
    skill = Skill(
        "retry",
        "Retries",
        "Check retries",
        "Bound retries.",
        metadata={"sourceant": {"review": False}},
        applications={"planning": True, "documentation": False},
    )
    store.write("one", skill, scope="workspace")
    read = SQLSkillLibrary(engine).one("one", "retry")
    assert read.reviews is False
    assert read.applies_to("planning") is True
    assert read.applies_to("documentation") is False
    assert read.applies_to("future-purpose") is None
    with engine.connect() as connection:
        assert "reviews" not in skill_table.c
        assert len(connection.execute(select(application_table)).all()) == 3
    store.write(
        "one",
        replace(skill, metadata={}, applications={"future-purpose": True}),
        scope="workspace",
    )
    read = store.one("one", "retry")
    assert read.reviews is None
    assert read.applies_to("planning") is None
    assert read.applies_to("future-purpose") is True
    assert store.one("two", "retry") is None
    store.forget("one", "retry", scope="workspace")
    with engine.connect() as connection:
        assert connection.execute(select(application_table)).all() == []


def test_skills_migration_matches_the_store(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from src.core import scopes
    from src.migrations.versions import create_skills

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    scopes.ensure(engine)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            create_skills.upgrade()
    library = SQLSkillLibrary(engine)
    library.write(
        "one", Skill("retry", "Retry", "Retries", "Bound retries."), scope="workspace"
    )
    assert library.one("one", "retry").body == "Bound retries."


@pytest.mark.asyncio
async def test_hosted_assembly_reads_sql_skills_without_a_plugin(tmp_path, monkeypatch):
    from mcp.server.auth.settings import AuthSettings
    from mcp.shared.memory import create_connected_server_and_client_session
    from src.core.mcp import hosted_surface
    from src.core.mcp.auth import SourceAntTokenVerifier
    from src.core.scope import Scope
    from src.core.services import ServiceRegistry
    from src.mcp_server import application

    engine = create_engine(f"sqlite:///{tmp_path / 'hosted.db'}")
    library = SQLSkillLibrary(engine, create_schema=True)
    library.write(
        "one", Skill("retry", "Retry", "Retries", "Bound retries."), scope="workspace"
    )
    monkeypatch.setattr(application, "get_engine", lambda: engine)
    monkeypatch.setattr(
        application, "_repositories", lambda engine: (None, None, None, None)
    )
    monkeypatch.setattr(application, "finding_store", lambda: None)
    monkeypatch.setattr(application, "service_registry", ServiceRegistry())
    surface = hosted_surface(
        auth=AuthSettings(
            issuer_url="https://issuer.example.com",
            resource_server_url="https://sourceant.example.com/mcp",
        ),
        token_verifier=SourceAntTokenVerifier(
            issuer="https://issuer.example.com",
            audience="sourceant-mcp",
            required_scopes=frozenset({"sourceant"}),
        ),
        scope_resolver=lambda scope: Scope.from_mapping({"workspace": "one"}),
    )
    server = application._assemble(surface)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("get_skill", {"scope": {}, "id": "retry"})
        assert not result.isError, result
        assert result.structuredContent["body"] == "Bound retries."
