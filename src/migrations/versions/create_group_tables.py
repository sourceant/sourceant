from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "groups_001"
down_revision = "skills_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "object_groups",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "parent_id", sa.String(length=255), nullable=False, server_default=""
        ),
        sa.Column(
            "external_ref", sa.String(length=500), nullable=False, server_default=""
        ),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )
    op.create_index(
        "ix_object_groups_scope_parent", "object_groups", ["scope_id", "parent_id"]
    )
    op.create_index(
        "ix_object_groups_scope_type", "object_groups", ["scope_id", "type"]
    )
    op.create_index(
        "ix_object_groups_scope_external_ref",
        "object_groups",
        ["scope_id", "external_ref"],
    )
    op.create_table(
        "object_group_members",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("group_id", sa.String(length=255), nullable=False),
        sa.Column(
            "member_scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("member_type", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint(
            "scope_id", "group_id", "member_scope_id", "member_type", "member_id"
        ),
    )
    op.create_index(
        "ix_object_group_members_reverse",
        "object_group_members",
        ["member_scope_id", "member_type", "member_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_object_group_members_reverse", table_name="object_group_members")
    op.drop_table("object_group_members")
    op.drop_index("ix_object_groups_scope_external_ref", table_name="object_groups")
    op.drop_index("ix_object_groups_scope_type", table_name="object_groups")
    op.drop_index("ix_object_groups_scope_parent", table_name="object_groups")
    op.drop_table("object_groups")
