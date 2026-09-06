from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "knowledge_links_001"
down_revision = "requirements_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_links",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("knowledge_id", sa.String(length=255), nullable=False),
        sa.Column("target_kind", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=500), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )
    op.create_index(
        "ix_knowledge_links_scope_knowledge",
        "knowledge_links",
        ["scope_id", "knowledge_id"],
    )
    op.create_index(
        "ix_knowledge_links_scope_target", "knowledge_links", ["scope_id", "target_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_links_scope_target", table_name="knowledge_links")
    op.drop_index("ix_knowledge_links_scope_knowledge", table_name="knowledge_links")
    op.drop_table("knowledge_links")
