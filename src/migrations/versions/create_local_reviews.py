"""Create local_reviews table

Revision ID: local_reviews_001
Revises: change_impact_001
Create Date: 2026-08-29 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "local_reviews_001"
down_revision = "change_impact_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("repository", sa.String(500), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False),
        # The whole answer, as it will be read back. Nothing queries inside it,
        # so taking it apart into columns would only give something else to
        # keep in step with the shape a screen expects.
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("started", sa.DateTime(), nullable=False),
        sa.Column("finished", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("local_reviews")
