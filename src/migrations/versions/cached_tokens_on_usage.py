"""What a model call read from a provider's cache, and what it wrote into one."""

import sqlalchemy as sa
from alembic import op

revision = "token_usage_002"
down_revision = "change_impact_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Zero on existing rows means the split was not recorded, not that no
    # cache was used. Their prompt totals already counted whatever was
    # served from one.
    op.add_column(
        "token_usage",
        sa.Column(
            "cached_input_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "token_usage",
        sa.Column(
            "cache_write_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("token_usage", "cache_write_tokens")
    op.drop_column("token_usage", "cached_input_tokens")
