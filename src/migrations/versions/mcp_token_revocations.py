import sqlalchemy as sa
from alembic import op

revision = "mcp_token_revocations_001"
down_revision = "requirement_acceptance_001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mcp_revoked_tokens",
        sa.Column("token_id", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("mcp_revoked_tokens")
