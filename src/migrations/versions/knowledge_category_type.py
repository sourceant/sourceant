import sqlalchemy as sa
from alembic import op

revision = "knowledge_type_001"
down_revision = "mcp_token_revocations_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "knowledge_objects",
        "kind",
        new_column_name="type",
        existing_type=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "knowledge_objects",
        "type",
        new_column_name="kind",
        existing_type=sa.String(255),
        existing_nullable=False,
    )
