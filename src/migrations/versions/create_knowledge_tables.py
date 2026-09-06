from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "knowledge_001"
down_revision = "configs_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every scoped table points at a scope by number rather than writing it out.
    # Created here because this is the first table that needs one. A scope grows
    # whenever it gains a qualifier, and MySQL will not build a key over 3072
    # bytes, so the scope itself is the only place it is written in full.
    op.create_table(
        "scopes",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        # Compared byte for byte. MySQL's default collation ignores case,
        # which would make one scope of two repositories whose names differ
        # only by it.
        sa.Column(
            "qualifiers",
            sa.String(length=500).with_variant(
                mysql.VARCHAR(500, collation="utf8mb4_bin"), "mysql"
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qualifiers", name="uq_scopes_qualifiers"),
    )
    op.create_table(
        "knowledge_objects",
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
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )
    op.create_table(
        "knowledge_relationships",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )


def downgrade() -> None:
    op.drop_table("knowledge_relationships")
    op.drop_table("knowledge_objects")
    op.drop_table("scopes")
