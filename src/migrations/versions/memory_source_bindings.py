from hashlib import sha256
import json

import sqlalchemy as sa
from alembic import op

revision = "memory_sources_001"
down_revision = "knowledge_type_001"
branch_labels = None
depends_on = None

DIMENSIONS = ("workspace", "repository", "system", "installation", "container")


def binding_digest(*values: str) -> str:
    return sha256(json.dumps(values, ensure_ascii=True).encode("ascii")).hexdigest()


def create_bindings_table(name: str) -> sa.Table:
    return op.create_table(
        name,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("repository_digest", sa.String(64), nullable=False),
        sa.Column("workspace_digest", sa.String(64), nullable=False),
        *(sa.Column(name, sa.Text, nullable=False) for name in DIMENSIONS),
    )


def create_scope_index() -> None:
    op.create_index(
        "ix_memory_source_bindings_scope",
        "memory_source_bindings",
        ["repository_digest", "workspace_digest"],
    )


def upgrade() -> None:
    create_bindings_table("memory_source_bindings")
    create_scope_index()


def downgrade() -> None:
    op.drop_table("memory_source_bindings")
