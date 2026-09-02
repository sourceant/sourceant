"""Where what a model call consumed is kept."""

import sqlalchemy as sa
from alembic import op

revision = "model_usage_001"
down_revision = "review_findings_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("workspace", sa.String(length=255), nullable=True),
        sa.Column("repository", sa.String(length=255), nullable=True),
        sa.Column("organization", sa.String(length=255), nullable=True),
        sa.Column("for_user", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_usage_model", "model_usage", ["model"])
    op.create_index("ix_model_usage_purpose", "model_usage", ["purpose"])
    op.create_index("ix_model_usage_workspace", "model_usage", ["workspace"])
    op.create_index("ix_model_usage_repository", "model_usage", ["repository"])


def downgrade() -> None:
    op.drop_index("ix_model_usage_repository", table_name="model_usage")
    op.drop_index("ix_model_usage_workspace", table_name="model_usage")
    op.drop_index("ix_model_usage_purpose", table_name="model_usage")
    op.drop_index("ix_model_usage_model", table_name="model_usage")
    op.drop_table("model_usage")
