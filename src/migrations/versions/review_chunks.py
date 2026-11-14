from alembic import op
import sqlalchemy as sa

revision = "review_chunks_001"
down_revision = "knowledge_type_001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace", sa.String(255), nullable=False),
        sa.Column("repository", sa.String(500), nullable=False),
        sa.Column("revision", sa.String(255), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("maximum_calls", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
    )
    op.create_table(
        "review_chunks",
        sa.Column("review_id", sa.String(64), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("group", sa.String(500), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
    )
    op.create_table(
        "review_responses",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("answer", sa.JSON(), nullable=False),
    )
    op.create_table(
        "execution_attempts",
        sa.Column("claim_token", sa.String(64), primary_key=True),
        sa.Column("resource", sa.String(255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.UniqueConstraint("resource", "attempt", name="uq_execution_attempt_number"),
    )
    op.create_index(
        "ix_execution_attempt_resource",
        "execution_attempts",
        ["resource", "status", "claim_expires_at"],
    )


def downgrade():
    op.drop_index("ix_execution_attempt_resource", table_name="execution_attempts")
    op.drop_table("execution_attempts")
    op.drop_table("review_responses")
    op.drop_table("review_chunks")
    op.drop_table("review_runs")
