from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "topology_001"
down_revision = "knowledge_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "topology_entities",
        sa.Column(
            "scope",
            sa.Text().with_variant(sa.String(length=500), "mysql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "id"),
    )
    op.create_table(
        "topology_relationships",
        sa.Column(
            "scope",
            sa.Text().with_variant(sa.String(length=500), "mysql"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "id"),
    )


def downgrade() -> None:
    op.drop_table("topology_relationships")
    op.drop_table("topology_entities")
