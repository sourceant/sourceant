"""Create review_records table

Revision ID: review_records_001
Revises: oauth_001
Create Date: 2026-02-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

revision = "review_records_001"
down_revision = "oauth_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_records",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("repository_full_name", sa.String(255), nullable=False, index=True),
        sa.Column("pr_number", sa.Integer, nullable=False, index=True),
        sa.Column("reviewed_head_sha", sa.String(64), nullable=False),
        sa.Column("reviewed_base_sha", sa.String(64), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="completed"),
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
    )
    op.create_index(
        "ix_review_records_repo_pr",
        "review_records",
        ["repository_full_name", "pr_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_records_repo_pr")
    op.drop_table("review_records")
