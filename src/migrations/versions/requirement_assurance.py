import sqlalchemy as sa
from alembic import op

revision = "requirement_assurance_001"
down_revision = "groups_002"
branch_labels = None
depends_on = None


def _create(name, *columns):
    op.create_table(
        name,
        sa.Column("scope_id", sa.BigInteger(), nullable=False, primary_key=True),
        *columns,
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.String(40), nullable=False),
        sa.Column("recorded_by", sa.String(255), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def upgrade():
    _create(
        "requirement_baselines",
        sa.Column("requirement_id", sa.String(255), primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
    )
    _create(
        "requirement_criteria",
        sa.Column("requirement_id", sa.String(255), primary_key=True),
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("previous_revision", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "scope_id",
            "requirement_id",
            "id",
            "previous_revision",
            name="uq_criterion_previous",
        ),
    )
    op.create_index(
        "ix_criterion_requirement",
        "requirement_criteria",
        ["scope_id", "requirement_id", "recorded_at"],
    )
    _create("requirement_evidence", sa.Column("id", sa.String(255), primary_key=True))
    op.create_index(
        "ix_evidence_recorded",
        "requirement_evidence",
        ["scope_id", "recorded_at", "id"],
    )
    _create(
        "requirement_assessments",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("requirement_id", sa.String(255), nullable=False),
        sa.Column("criterion_id", sa.String(255), nullable=False),
        sa.Column("supersedes", sa.String(255), nullable=False),
        sa.UniqueConstraint(
            "scope_id",
            "requirement_id",
            "criterion_id",
            "supersedes",
            name="uq_assessment_previous",
        ),
    )
    op.create_index(
        "ix_assessment_criterion",
        "requirement_assessments",
        ["scope_id", "requirement_id", "criterion_id", "recorded_at"],
    )
    op.create_table(
        "requirement_assessment_evidence",
        sa.Column("scope_id", sa.BigInteger(), primary_key=True),
        sa.Column("assessment_id", sa.String(255), primary_key=True),
        sa.Column("evidence_id", sa.String(255), primary_key=True),
    )
    op.create_index(
        "ix_evidence_assessments",
        "requirement_assessment_evidence",
        ["scope_id", "evidence_id"],
    )


def downgrade():
    for name in (
        "requirement_assessment_evidence",
        "requirement_assessments",
        "requirement_evidence",
        "requirement_criteria",
        "requirement_baselines",
    ):
        op.drop_table(name)
