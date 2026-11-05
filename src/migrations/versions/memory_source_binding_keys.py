import sqlalchemy as sa
from alembic import op

from src.migrations.versions.memory_source_bindings import (
    DIMENSIONS,
    binding_digest,
    create_bindings_table,
    create_scope_index,
)

revision = "memory_sources_002"
down_revision = "memory_sources_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = sa.inspect(connection).get_columns("memory_source_bindings")
    if any(column["name"] == "id" for column in columns):
        return
    old = sa.Table("memory_source_bindings", sa.MetaData(), autoload_with=connection)
    replacement = create_bindings_table("memory_source_bindings_replacement")
    rows = connection.execute(sa.select(old))
    for batch in rows.mappings().partitions(500):
        connection.execute(
            replacement.insert(),
            [
                dict(
                    **row,
                    id=binding_digest(*(row[name] for name in DIMENSIONS)),
                    repository_digest=binding_digest(row["repository"]),
                    workspace_digest=binding_digest(row["workspace"]),
                )
                for row in batch
            ],
        )
    rows.close()
    op.drop_table("memory_source_bindings")
    op.rename_table("memory_source_bindings_replacement", "memory_source_bindings")
    create_scope_index()


def downgrade() -> None:
    # Revision 001 now creates this same schema, including on MySQL.
    pass
