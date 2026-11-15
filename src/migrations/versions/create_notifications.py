import sqlalchemy as sa
from alembic import op

revision = "notifications_001"
down_revision = "mcp_token_revocations_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace", sa.String(255), nullable=False, index=True),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("action_url", sa.String(500), nullable=False),
        sa.Column("action_label", sa.String(100), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "notification_emails",
        sa.Column(
            "notification_id",
            sa.String(64),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("lease", sa.String(), nullable=False),
        sa.Column("leased_until", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "notification_reads",
        sa.Column(
            "notification_id",
            sa.String(64),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("read_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notification_emails")
    op.drop_table("notification_reads")
    op.drop_table("notifications")
