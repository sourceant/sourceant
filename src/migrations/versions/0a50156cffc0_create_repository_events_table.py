"""Create Repository Events table

Revision ID: 0a50156cffc0
Revises: fd1de25ef201
Create Date: 2025-01-29 21:24:15.533322

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import func

# revision identifiers, used by Alembic.
revision: str = "0a50156cffc0"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repository_events",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("provider", sa.String, index=True),
        sa.Column("type", sa.String),
        sa.Column("action", sa.String),
        sa.Column("number", sa.Integer),
        sa.Column("repository_full_name", sa.String),
        sa.Column("url", sa.String),
        sa.Column("title", sa.String),
        sa.Column("payload", postgresql.JSON),
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


def downgrade() -> None:
    op.drop_table("repository_events")
