"""Create configs table

Revision ID: configs_001
Revises: review_records_001
Create Date: 2026-02-17 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

revision = "configs_001"
down_revision = "connected_repos_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "configs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("configurable_type", sa.String, nullable=False),
        sa.Column("configurable_id", sa.String, nullable=False),
        sa.Column("key", sa.String, nullable=False),
        sa.Column("value", sa.String, nullable=False),
        sa.Column("type", sa.String, nullable=False, server_default="string"),
        sa.Column(
            "created_at", sa.DateTime(), server_default=func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "configurable_type",
            "configurable_id",
            "key",
            name="uq_config_entry",
        ),
    )
    op.create_index(
        "ix_configs_entity",
        "configs",
        ["configurable_type", "configurable_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_configs_entity")
    op.drop_table("configs")
