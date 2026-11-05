import sqlalchemy as sa
from alembic import op

revision = "memory_sources_001"
down_revision = "knowledge_type_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_source_bindings",
        *(
            sa.Column(name, sa.String(255), primary_key=True)
            for name in (
                "workspace",
                "repository",
                "system",
                "installation",
                "container",
            )
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_source_bindings")
