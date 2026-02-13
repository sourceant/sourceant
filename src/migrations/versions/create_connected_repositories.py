"""Create connected_repositories table

Revision ID: connected_repos_001
Revises: repositories_001
Create Date: 2026-02-12 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

revision = "connected_repos_001"
down_revision = "repositories_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connected_repositories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False, index=True),
        sa.Column(
            "repository_id",
            sa.Integer,
            sa.ForeignKey("repositories.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "connected_at", sa.DateTime(), server_default=func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "repository_id", name="uq_user_repository"),
    )


def downgrade() -> None:
    op.drop_table("connected_repositories")
