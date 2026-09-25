import sqlalchemy as sa
from alembic import op

revision = "skills_001"
down_revision = "cache_entries_001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skills",
        sa.Column(
            "scope_id", sa.BigInteger(), sa.ForeignKey("scopes.id"), nullable=False
        ),
        sa.Column("id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )
    op.create_table(
        "skill_applications",
        sa.Column("scope_id", sa.BigInteger(), nullable=False),
        sa.Column("skill_id", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "skill_id", "purpose"),
        sa.ForeignKeyConstraint(
            ["scope_id", "skill_id"],
            ["skills.scope_id", "skills.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_skill_applications_scope_purpose",
        "skill_applications",
        ["scope_id", "purpose", "enabled"],
    )


def downgrade():
    op.drop_table("skill_applications")
    op.drop_table("skills")
