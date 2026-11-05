import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from src.config import settings
from src.migrations.versions import knowledge_category_type


def test_knowledge_category_migration_preserves_data_in_both_directions(
    tmp_path, monkeypatch
):
    url = f"sqlite:///{tmp_path / 'knowledge.db'}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, knowledge_category_type.down_revision)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO scopes (id, qualifiers) VALUES (1, '{}')")
        )
        connection.execute(
            sa.text(
                "INSERT INTO knowledge_objects "
                "(scope_id, id, kind, status, summary, properties) "
                "VALUES (1, 'decision', 'decision', 'accepted', 'Use UTC', '{}')"
            )
        )
    command.upgrade(config, knowledge_category_type.revision)
    assert {
        column["name"] for column in sa.inspect(engine).get_columns("knowledge_objects")
    } == {"scope_id", "id", "type", "status", "summary", "properties"}
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT type, summary FROM knowledge_objects")
        ).one() == ("decision", "Use UTC")
    command.downgrade(config, knowledge_category_type.down_revision)
    columns = {
        column["name"] for column in sa.inspect(engine).get_columns("knowledge_objects")
    }
    assert "kind" in columns
    assert "type" not in columns
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("SELECT kind, summary FROM knowledge_objects")
        ).one() == ("decision", "Use UTC")
    engine.dispose()
