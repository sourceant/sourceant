"""Bring databases made before the rename onto the current names.

A revision is recorded as applied by its id, so a database that ran the
knowledge or the workspaces revision before either was renamed keeps the old
table and column permanently, and the code expecting the new ones fails on
first read.

Each rename is guarded, so this does nothing where the names are already
current.
"""

import sqlalchemy as sa
from alembic import op

revision = "core_renames_001"
down_revision = "review_findings_001"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    columns = sa.inspect(op.get_bind()).get_columns(table)
    return any(one["name"] == column for one in columns)


def upgrade() -> None:
    if _has_table("knowledge") and not _has_table("knowledge_objects"):
        op.rename_table("knowledge", "knowledge_objects")

    if _has_column("workspaces", "external_id") and not _has_column(
        "workspaces", "external_ref"
    ):
        op.alter_column("workspaces", "external_id", new_column_name="external_ref")


def downgrade() -> None:
    if _has_table("knowledge_objects") and not _has_table("knowledge"):
        op.rename_table("knowledge_objects", "knowledge")

    if _has_column("workspaces", "external_ref") and not _has_column(
        "workspaces", "external_id"
    ):
        op.alter_column("workspaces", "external_ref", new_column_name="external_id")
