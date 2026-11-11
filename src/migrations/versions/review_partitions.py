from alembic import op
import sqlalchemy as sa

revision = "review_partitions_001"
down_revision = "knowledge_type_001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_partition_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace", sa.String(255), nullable=False),
        sa.Column("repository", sa.String(500), nullable=False),
        sa.Column("revision", sa.String(255), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("maximum_calls", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
    )
    op.create_table(
        "review_partition_parts",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("lease", sa.String(64), nullable=False),
        sa.Column("leased_until", sa.DateTime(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
    )
    op.create_table(
        "review_partition_responses",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("answer", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("review_partition_responses")
    op.drop_table("review_partition_parts")
    op.drop_table("review_partition_runs")
