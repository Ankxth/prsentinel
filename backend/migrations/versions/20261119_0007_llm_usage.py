"""llm_usage: daily token allowance bookkeeping (feature F)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["repos.id"], name=op.f("fk_llm_usage_repo_id_repos"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_usage")),
    )
    op.create_index(op.f("ix_llm_usage_repo_id"), "llm_usage", ["repo_id"], unique=False)
    op.create_index(
        "ix_llm_usage_user_id_created_at", "llm_usage", ["user_id", "created_at"], unique=False
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE llm_usage ENABLE ROW LEVEL SECURITY")
        op.execute(
            """
            DO $$
            BEGIN
              IF to_regprocedure('auth.uid()') IS NOT NULL THEN
                CREATE POLICY llm_usage_select_own ON llm_usage FOR SELECT USING (
                  user_id = auth.uid()
                );
              END IF;
            END $$
            """
        )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_user_id_created_at", table_name="llm_usage")
    op.drop_index(op.f("ix_llm_usage_repo_id"), table_name="llm_usage")
    op.drop_table("llm_usage")
