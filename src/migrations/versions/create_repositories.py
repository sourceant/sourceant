"""Create repositories table

Revision ID: repositories_001
Revises: review_records_001
Create Date: 2026-02-12 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

revision = "repositories_001"
down_revision = "review_records_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("provider", sa.String, nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("full_name", sa.String, nullable=False, index=True),
        sa.Column("url", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=True),
        sa.Column("private", sa.Boolean, nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False),
        sa.Column("visibility", sa.String, nullable=False),
        sa.Column("owner", sa.String, nullable=False),
        sa.Column("owner_type", sa.String, nullable=False),
        sa.Column("language", sa.String, nullable=True),
        sa.Column("default_branch", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("repositories")
