from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "workspaces_002"
down_revision = "code_index_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.alter_column(
            "external_id",
            new_column_name="external_ref",
            existing_type=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch:
        batch.alter_column(
            "external_ref",
            new_column_name="external_id",
            existing_type=sa.String(length=255),
            existing_nullable=False,
        )
