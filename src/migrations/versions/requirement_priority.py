import json

import sqlalchemy as sa
from alembic import op

revision = "requirement_priority_001"
down_revision = "cached_reviews_001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "requirements",
        sa.Column("priority", sa.String(255), nullable=False, server_default=""),
    )
    table = sa.table(
        "requirements",
        sa.column("scope_id"),
        sa.column("id"),
        sa.column("properties"),
        sa.column("priority"),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).mappings():
        priority = str(json.loads(row["properties"]).get("priority") or "")
        connection.execute(
            table.update()
            .where(table.c.scope_id == row["scope_id"], table.c.id == row["id"])
            .values(priority=priority)
        )
    op.create_index(
        "ix_requirements_scope_priority", "requirements", ["scope_id", "priority"]
    )


def downgrade():
    op.drop_index("ix_requirements_scope_priority", table_name="requirements")
    op.drop_column("requirements", "priority")
