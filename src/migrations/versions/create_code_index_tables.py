from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "code_index_001"
down_revision = "topology_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "code_nodes",
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("id", sa.String(length=500), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "id"),
    )
    op.create_index(
        "ix_code_nodes_scope_file_path", "code_nodes", ["scope", "file_path"]
    )
    op.create_table(
        "code_node_labels",
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("node_id", sa.String(length=500), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("scope", "node_id", "label"),
    )
    op.create_index(
        "ix_code_node_labels_scope_label", "code_node_labels", ["scope", "label"]
    )
    op.create_table(
        "code_edges",
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("id", sa.String(length=500), nullable=False),
        sa.Column("source_id", sa.String(length=500), nullable=False),
        sa.Column("target_id", sa.String(length=500), nullable=False),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("properties", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "id"),
    )
    op.create_index("ix_code_edges_scope_source", "code_edges", ["scope", "source_id"])
    op.create_index("ix_code_edges_scope_target", "code_edges", ["scope", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_code_edges_scope_target", table_name="code_edges")
    op.drop_index("ix_code_edges_scope_source", table_name="code_edges")
    op.drop_table("code_edges")
    op.drop_index("ix_code_node_labels_scope_label", table_name="code_node_labels")
    op.drop_table("code_node_labels")
    op.drop_index("ix_code_nodes_scope_file_path", table_name="code_nodes")
    op.drop_table("code_nodes")
