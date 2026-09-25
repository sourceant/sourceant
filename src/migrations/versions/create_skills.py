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
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reviews", sa.Boolean(), nullable=True),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("paths", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("scope_id", "id"),
    )


def downgrade():
    op.drop_table("skills")
