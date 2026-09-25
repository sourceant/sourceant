from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "groups_002"
down_revision = "groups_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL counts four bytes a character towards the 3072 byte key limit, and
    # the key this column sits in came to 3292. Batch mode because SQLite
    # rewrites the table rather than altering a column in place.
    with op.batch_alter_table("object_group_members") as batch:
        batch.alter_column(
            "member_id",
            existing_type=sa.String(length=500),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("object_group_members") as batch:
        batch.alter_column(
            "member_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=500),
            existing_nullable=False,
        )
