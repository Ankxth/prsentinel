"""findings: scanner results per run (feature E1)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.String(length=20), nullable=False),
        sa.Column("rule_id", sa.String(length=60), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("confidence", sa.String(length=10), nullable=False),
        sa.Column("file", sa.String(length=500), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(length=400), nullable=False),
        sa.Column("cwe", sa.String(length=20), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_findings_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.UniqueConstraint("run_id", "fingerprint", name="uq_findings_run_id_fingerprint"),
    )
    op.create_index(op.f("ix_findings_run_id"), "findings", ["run_id"], unique=False)
    op.create_index(op.f("ix_findings_fingerprint"), "findings", ["fingerprint"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE findings ENABLE ROW LEVEL SECURITY")
        op.execute(
            """
            DO $$
            BEGIN
              IF to_regprocedure('auth.uid()') IS NOT NULL THEN
                CREATE POLICY findings_select_own ON findings FOR SELECT USING (
                  run_id IN (
                    SELECT r.id FROM runs r JOIN repos p ON p.id = r.repo_id
                    WHERE p.owner_user_id = auth.uid()
                  )
                );
              END IF;
            END $$
            """
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_findings_fingerprint"), table_name="findings")
    op.drop_index(op.f("ix_findings_run_id"), table_name="findings")
    op.drop_table("findings")
