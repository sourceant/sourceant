"""The queue has to build on every database a deployment might use.

MySQL counts four bytes a character in an index and refuses a key over 3072 of
them, which is what has already caught this estate once. A test is cheaper than
finding out when somebody's migration fails.
"""

from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from src.core.jobs.sql import job_batch_table, job_lock_table, job_table

TABLES = (job_table, job_lock_table, job_batch_table)


def _mysql_bytes(columns) -> int:
    """What MySQL counts these columns as, reserving four bytes a character."""
    total = 0
    for column in columns:
        kind = column.type.dialect_impl(mysql.dialect())
        total += (kind.length * 4) if getattr(kind, "length", None) else 8
    return total


def test_every_key_fits_inside_what_mysql_allows():
    for table in TABLES:
        assert _mysql_bytes(table.primary_key) <= 3072, table.name
        for index in table.indexes:
            assert _mysql_bytes(index.columns) <= 3072, f"{table.name}.{index.name}"


def test_the_tables_build_on_mysql_and_postgres():
    for table in TABLES:
        for dialect in (mysql.dialect(), postgresql.dialect()):
            ddl = str(CreateTable(table).compile(dialect=dialect))
            assert table.name in ddl


def test_at_most_one_queued_job_holds_a_dedupe_key():
    """A plain unique index rather than a partial one, because MySQL has no
    partial indexes. A finished job gives its key up so the same work can be
    asked for again."""
    unique = [index for index in job_table.indexes if index.unique]

    assert [index.name for index in unique] == ["ux_jobs_dedupe"]
    for dialect in (mysql.dialect(), postgresql.dialect()):
        ddl = str(CreateIndex(unique[0]).compile(dialect=dialect))
        assert "UNIQUE" in ddl and "dedupe_slot" in ddl


def test_no_column_type_is_available_on_only_one_database():
    """Nothing here may use a type that would tie the core to Postgres. The
    estate has just paid to keep MySQL working."""
    for table in TABLES:
        for dialect in (mysql.dialect(), postgresql.dialect()):
            for column in table.columns:
                assert column.type.dialect_impl(dialect) is not None
