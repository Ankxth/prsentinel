"""proof agent: finding_attempts and proof columns on findings (feature E3)

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("proof_status", sa.String(length=20), nullable=True))
    op.add_column("findings", sa.Column("proof_reason", sa.String(length=300), nullable=True))
    op.add_column("findings", sa.Column("proof_model", sa.String(length=80), nullable=True))

    op.create_table(
        "finding_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("test_code", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("lint_ok", sa.Boolean(), nullable=True),
        sa.Column("lint_errors", sa.JSON(), nullable=True),
        sa.Column("outcomes", sa.JSON(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_finding_attempts_finding_id_findings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finding_attempts")),
        sa.UniqueConstraint(
            "finding_id", "attempt", name="uq_finding_attempts_finding_id_attempt"
        ),
    )
    op.create_index(
        op.f("ix_finding_attempts_finding_id"), "finding_attempts", ["finding_id"], unique=False
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE finding_attempts ENABLE ROW LEVEL SECURITY")
        op.execute(
            """
            DO $$
            BEGIN
              IF to_regprocedure('auth.uid()') IS NOT NULL THEN
                CREATE POLICY finding_attempts_select_own ON finding_attempts FOR SELECT USING (
                  finding_id IN (
                    SELECT f.id FROM findings f
                    JOIN runs r ON r.id = f.run_id
                    JOIN repos p ON p.id = r.repo_id
                    WHERE p.owner_user_id = auth.uid()
                  )
                );
              END IF;
            END $$
            """
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_finding_attempts_finding_id"), table_name="finding_attempts")
    op.drop_table("finding_attempts")
    op.drop_column("findings", "proof_model")
    op.drop_column("findings", "proof_reason")
    op.drop_column("findings", "proof_status")
