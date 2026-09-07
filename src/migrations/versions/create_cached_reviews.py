"""Where a generated review is kept for reuse."""

import sqlalchemy as sa
from alembic import op

revision = "cached_reviews_001"
down_revision = "jobs_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cached_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_cached_reviews_key"),
    )
    op.create_index("ix_cached_reviews_expires_at", "cached_reviews", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_cached_reviews_expires_at", table_name="cached_reviews")
    op.drop_table("cached_reviews")
