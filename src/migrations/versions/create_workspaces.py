"""Create workspaces table

Revision ID: workspaces_001
Revises: repositories_001
Create Date: 2026-08-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import func

revision = "workspaces_001"
down_revision = "repositories_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer, primary_key=True),
        # The identity everything outside this deployment uses, and what a token
        # names. Unique, so what belongs to a workspace can point straight at it.
        sa.Column(
            "external_id", sa.String(255), nullable=False, unique=True, index=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("workspaces")
