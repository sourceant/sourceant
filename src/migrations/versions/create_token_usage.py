"""Where what a model call consumed is kept."""

import sqlalchemy as sa
from alembic import op

revision = "token_usage_001"
down_revision = "review_findings_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reported_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_micro", sa.BigInteger(), nullable=True),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="USD"
        ),
        sa.Column("owner_type", sa.String(length=255), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=True),
        sa.Column("subject_type", sa.String(length=255), nullable=True),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_token_usage_model", "token_usage", ["model"])
    op.create_index("ix_token_usage_provider", "token_usage", ["provider"])
    op.create_index(
        "ix_token_usage_owner_when",
        "token_usage",
        ["owner_type", "owner_id", "created_at"],
    )
    op.create_index(
        "ix_token_usage_owner_purpose",
        "token_usage",
        ["owner_type", "owner_id", "purpose"],
    )
    op.create_index(
        "ix_token_usage_subject", "token_usage", ["subject_type", "subject_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_token_usage_subject", table_name="token_usage")
    op.drop_index("ix_token_usage_owner_purpose", table_name="token_usage")
    op.drop_index("ix_token_usage_owner_when", table_name="token_usage")
    op.drop_index("ix_token_usage_provider", table_name="token_usage")
    op.drop_index("ix_token_usage_model", table_name="token_usage")
    op.drop_table("token_usage")
