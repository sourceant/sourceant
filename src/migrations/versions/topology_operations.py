import sqlalchemy as sa
from alembic import op

revision = "topology_operations_001"
down_revision = "cached_reviews_001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "topology_operations",
        sa.Column("scope_id", sa.BigInteger(), primary_key=True),
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
    )


def downgrade():
    op.drop_table("topology_operations")
