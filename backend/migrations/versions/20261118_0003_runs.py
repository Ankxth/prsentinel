"""runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("github_run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("sha", sa.String(length=64), nullable=False),
        sa.Column("ref", sa.String(length=255), nullable=False),
        sa.Column("event", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", JsonType, nullable=False),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_runs_repo_id_repos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint(
            "repo_id",
            "github_run_id",
            "run_attempt",
            name=op.f("uq_runs_repo_id_github_run_id_run_attempt"),
        ),
    )
    op.create_index(op.f("ix_runs_repo_id"), "runs", ["repo_id"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE runs ENABLE ROW LEVEL SECURITY")
        op.execute(
            """
            DO $$
            BEGIN
              IF to_regprocedure('auth.uid()') IS NOT NULL THEN
                CREATE POLICY runs_select_own ON runs FOR SELECT USING (
                  repo_id IN (SELECT id FROM repos WHERE owner_user_id = auth.uid())
                );
              END IF;
            END $$
            """
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_runs_repo_id"), table_name="runs")
    op.drop_table("runs")
