"""Create review_findings table

Revision ID: review_findings_001
Revises: local_reviews_001
"""

import sqlalchemy as sa
from alembic import op

revision = "review_findings_001"
down_revision = "local_reviews_001"
branch_labels = None
depends_on = None


# A scope is a provider and an owner/name. It is sized rather than left as
# text because it is half the primary key, and MySQL cannot index text.
SCOPE = 191


def upgrade() -> None:
    op.create_table(
        "review_findings",
        sa.Column("scope", sa.String(length=SCOPE), nullable=False),
        # A fingerprint rather than a position: an edit above a finding moves
        # its line, and an identity that moves loses whatever state somebody
        # set on it.
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, index=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("code_anchor", sa.String(500), nullable=True),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scope", "id"),
    )


def downgrade() -> None:
    op.drop_table("review_findings")
