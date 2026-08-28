from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "change_impact_001"
down_revision = "knowledge_links_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "impact_code_mappings",
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("change_kind", sa.String(length=255), nullable=False),
        sa.Column("change_id", sa.String(length=500), nullable=False),
        sa.Column("revision", sa.String(length=255), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint(
            "scope", "change_kind", "change_id", "revision", "entity_id"
        ),
    )
    op.create_index(
        "ix_impact_code_mappings_scope_change",
        "impact_code_mappings",
        ["scope", "change_kind", "change_id"],
    )
    op.create_table(
        "compatibility_checks",
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("provider_entity_id", sa.String(length=255), nullable=False),
        sa.Column("consumer_entity_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=255), nullable=False),
        sa.Column("compatible", sa.Boolean(), nullable=True),
        sa.Column("before_revision", sa.String(length=255), nullable=False),
        sa.Column("after_revision", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "id"),
    )
    op.create_index(
        "ix_compatibility_checks_scope_provider",
        "compatibility_checks",
        ["scope", "provider_entity_id"],
    )
    op.create_index(
        "ix_compatibility_checks_scope_consumer",
        "compatibility_checks",
        ["scope", "consumer_entity_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_compatibility_checks_scope_consumer",
        table_name="compatibility_checks",
    )
    op.drop_index(
        "ix_compatibility_checks_scope_provider",
        table_name="compatibility_checks",
    )
    op.drop_table("compatibility_checks")
    op.drop_index(
        "ix_impact_code_mappings_scope_change", table_name="impact_code_mappings"
    )
    op.drop_table("impact_code_mappings")
