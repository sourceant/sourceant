from alembic import op
import sqlalchemy as sa

revision = "review_chunks_002"
down_revision = "review_chunks_001"
branch_labels = None
depends_on = None


def _namespace(old, new):
    connection = op.get_bind()
    if "cache_entries" in sa.inspect(connection).get_table_names():
        table = sa.Table("cache_entries", sa.MetaData(), autoload_with=connection)
        connection.execute(
            sa.update(table).where(table.c.namespace == old).values(namespace=new)
        )


def upgrade():
    op.rename_table("review_partition_runs", "review_runs")
    op.rename_table("review_partition_responses", "review_responses")
    op.add_column(
        "review_chunks", sa.Column("diff", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "review_chunks",
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    connection = op.get_bind()
    runs = sa.Table("review_runs", sa.MetaData(), autoload_with=connection)
    chunks = sa.Table("review_chunks", sa.MetaData(), autoload_with=connection)
    for row in connection.execute(sa.select(runs)).mappings():
        for part in row["plan"]["parts"]:
            connection.execute(
                sa.update(chunks)
                .where(chunks.c.review_id == row["id"], chunks.c.id == part["id"])
                .values(input_tokens=part.get("input_tokens", 0))
            )
    _namespace("review.partition-run", "review.run")


def downgrade():
    _namespace("review.run", "review.partition-run")
    with op.batch_alter_table("review_chunks") as batch:
        batch.drop_column("input_tokens")
        batch.drop_column("diff")
    op.rename_table("review_responses", "review_partition_responses")
    op.rename_table("review_runs", "review_partition_runs")
