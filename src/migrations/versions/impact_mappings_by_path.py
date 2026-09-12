from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "change_impact_002"
down_revision = "topology_operations_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keyed on what a change is rather than on the commit it was seen at: a
    # kind, a repository and a path, hashed into one column because together
    # they exceed what MySQL allows in a key.
    #
    # Nothing is carried over. Every existing row is keyed on a revision no
    # review will ever ask for again, so there is nothing in them to keep.
    op.drop_index(
        "ix_impact_code_mappings_scope_change", table_name="impact_code_mappings"
    )
    op.drop_table("impact_code_mappings")
    op.create_table(
        "impact_code_mappings",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("reference_key", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("change_kind", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column("change_path", sa.String(length=383), nullable=False),
        sa.Column("revision", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "reference_key", "entity_id"),
    )


def downgrade() -> None:
    op.drop_table("impact_code_mappings")
    op.create_table(
        "impact_code_mappings",
        sa.Column(
            "scope_id",
            sa.BigInteger(),
            sa.ForeignKey("scopes.id"),
            nullable=False,
        ),
        sa.Column("change_kind", sa.String(length=64), nullable=False),
        sa.Column("change_id", sa.String(length=383), nullable=False),
        sa.Column("revision", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint(
            "scope_id", "change_kind", "change_id", "revision", "entity_id"
        ),
    )
    op.create_index(
        "ix_impact_code_mappings_scope_change",
        "impact_code_mappings",
        ["scope_id", "change_kind", "change_id"],
    )
