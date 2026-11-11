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
        sa.Column("lease", sa.String(64), nullable=False),
        sa.Column("leased_until", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "review_responses",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("answer", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("review_responses")
    op.drop_table("review_chunks")
    op.drop_table("review_runs")
