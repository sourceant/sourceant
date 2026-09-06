"""Background work, the locks that keep it from overlapping, and its batches."""

import sqlalchemy as sa
from alembic import op

revision = "jobs_001"
down_revision = "token_usage_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("lane", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=255), nullable=False),
        sa.Column("tenant", sa.String(length=255), nullable=False, server_default=""),
        sa.Column(
            "tenant_kind", sa.String(length=32), nullable=False, server_default=""
        ),
        sa.Column("scope_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column(
            "deadline_seconds", sa.Integer(), nullable=False, server_default="300"
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("exclusive_key", sa.String(length=255), nullable=True),
        sa.Column("dedupe_slot", sa.String(length=255), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("leased_by", sa.String(length=255), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jobs_ready", "jobs", ["lane", "state", "available_at", "priority", "id"]
    )
    op.create_index("ix_jobs_tenant", "jobs", ["lane", "state", "tenant"])
    op.create_index("ix_jobs_lease", "jobs", ["state", "lease_until"])
    # At most one queued job may hold a key. A finished job is given a key of
    # its own so the same work can be asked for again, which is why this is a
    # plain unique index rather than a partial one MySQL could not build.
    op.create_index("ux_jobs_dedupe", "jobs", ["dedupe_slot"], unique=True)

    op.create_table(
        "job_locks",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "job_batches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tenant", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("job_batches")
    op.drop_table("job_locks")
    op.drop_index("ux_jobs_dedupe", table_name="jobs")
    op.drop_index("ix_jobs_lease", table_name="jobs")
    op.drop_index("ix_jobs_tenant", table_name="jobs")
    op.drop_index("ix_jobs_ready", table_name="jobs")
    op.drop_table("jobs")
