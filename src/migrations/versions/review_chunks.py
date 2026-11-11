import json
from posixpath import commonpath, dirname

from alembic import op
import sqlalchemy as sa

revision = "review_chunks_001"
down_revision = "review_partitions_001"
branch_labels = None
depends_on = None


def _document_column(table_name, column_name, target):
    connection = op.get_bind()
    table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
    if connection.dialect.name == "sqlite":
        table.c[column_name].type = target
        with op.batch_alter_table(table_name, copy_from=table, recreate="always"):
            pass
    else:
        options = {}
        if connection.dialect.name == "postgresql":
            cast = "json" if isinstance(target, sa.JSON) else "text"
            options["postgresql_using"] = f'"{column_name}"::{cast}'
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                column_name,
                existing_type=table.c[column_name].type,
                type_=target,
                existing_nullable=table.c[column_name].nullable,
                **options,
            )


def upgrade():
    connection = op.get_bind()
    old = sa.Table("review_partition_parts", sa.MetaData(), autoload_with=connection)
    runs = sa.Table("review_partition_runs", sa.MetaData(), autoload_with=connection)
    plans = {
        row["id"]: {part["id"]: part for part in json.loads(row["plan"])["parts"]}
        for row in connection.execute(sa.select(runs)).mappings()
    }
    table = op.create_table(
        "review_chunks",
        sa.Column("review_id", sa.String(64), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("group", sa.String(500), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("lease", sa.String(64), nullable=False),
        sa.Column("leased_until", sa.DateTime(), nullable=True),
    )
    for row in connection.execute(sa.select(old)).mappings():
        part = plans[row["run_id"]][row["id"]]
        files = part.get("targets", [])
        connection.execute(
            sa.insert(table).values(
                review_id=row["run_id"],
                id=row["id"],
                group=part.get(
                    "group",
                    (
                        commonpath([dirname(file["path"]) or "." for file in files])
                        if files
                        else ""
                    ),
                ),
                files=files,
                status="pending" if row["state"] == "queued" else row["state"],
                attempts=0 if row["state"] == "queued" else 1,
                result=json.loads(row["answer"]) if row["answer"] else None,
                lease=row["lease"],
                leased_until=row["leased_until"],
            )
        )
    op.drop_table("review_partition_parts")
    _document_column("review_partition_runs", "plan", sa.JSON())
    _document_column("review_partition_responses", "answer", sa.JSON())


def downgrade():
    connection = op.get_bind()
    chunks = sa.Table("review_chunks", sa.MetaData(), autoload_with=connection)
    table = op.create_table(
        "review_partition_parts",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("lease", sa.String(64), nullable=False),
        sa.Column("leased_until", sa.DateTime(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
    )
    for row in connection.execute(sa.select(chunks)).mappings():
        connection.execute(
            sa.insert(table).values(
                run_id=row["review_id"],
                id=row["id"],
                state="queued" if row["status"] == "pending" else row["status"],
                lease=row["lease"],
                leased_until=row["leased_until"],
                answer=json.dumps(row["result"]) if row["result"] is not None else None,
            )
        )
    op.drop_table("review_chunks")
    _document_column("review_partition_runs", "plan", sa.Text())
    _document_column("review_partition_responses", "answer", sa.Text())
