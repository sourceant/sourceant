from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "requirements_001"
down_revision = "code_index_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "requirements",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.String(length=500), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )
    op.create_index(
        "ix_requirements_scope_external_ref",
        "requirements",
        ["scope_id", "external_ref"],
    )
    op.create_table(
        "requirement_links",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("requirement_id", sa.String(length=255), nullable=False),
        sa.Column("target_kind", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=500), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )
    op.create_index(
        "ix_requirement_links_scope_requirement",
        "requirement_links",
        ["scope_id", "requirement_id"],
    )
    op.create_index(
        "ix_requirement_links_scope_target",
        "requirement_links",
        ["scope_id", "target_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_requirement_links_scope_target", table_name="requirement_links")
    op.drop_index(
        "ix_requirement_links_scope_requirement", table_name="requirement_links"
    )
    op.drop_table("requirement_links")
    op.drop_index("ix_requirements_scope_external_ref", table_name="requirements")
    op.drop_table("requirements")
