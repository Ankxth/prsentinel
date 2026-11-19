"""run_functions and test_results (feature D)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "run_functions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("file", sa.String(length=500), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("change_type", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("test_code", sa.Text(), nullable=True),
        sa.Column("cache_key", sa.String(length=64), nullable=True),
        sa.Column("mutation", JsonType, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_functions_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_functions")),
        sa.UniqueConstraint("run_id", "file", "name", name=op.f("uq_run_functions_run_id_file_name")),
    )
    op.create_index(op.f("ix_run_functions_run_id"), "run_functions", ["run_id"], unique=False)
    op.create_index(op.f("ix_run_functions_cache_key"), "run_functions", ["cache_key"], unique=False)

    op.create_table(
        "test_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_function_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("test_name", sa.String(length=300), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("before_result", sa.String(length=10), nullable=False),
        sa.Column("after_result", sa.String(length=10), nullable=False),
        sa.Column("label", sa.String(length=24), nullable=False),
        sa.Column("rerun_passes", sa.Integer(), nullable=False),
        sa.Column("rerun_fails", sa.Integer(), nullable=False),
        sa.Column("diagnosis", sa.String(length=12), nullable=True),
        sa.Column("reason", sa.String(length=400), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["run_function_id"],
            ["run_functions.id"],
            name=op.f("fk_test_results_run_function_id_run_functions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_test_results_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_results")),
    )
    op.create_index(
        op.f("ix_test_results_run_function_id"), "test_results", ["run_function_id"], unique=False
    )
    op.create_index("ix_test_results_run_id", "test_results", ["run_id"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        for table in ("run_functions", "test_results"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"""
                DO $$
                BEGIN
                  IF to_regprocedure('auth.uid()') IS NOT NULL THEN
                    CREATE POLICY {table}_select_own ON {table} FOR SELECT USING (
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
    op.drop_index("ix_test_results_run_id", table_name="test_results")
    op.drop_index(op.f("ix_test_results_run_function_id"), table_name="test_results")
    op.drop_table("test_results")
    op.drop_index(op.f("ix_run_functions_cache_key"), table_name="run_functions")
    op.drop_index(op.f("ix_run_functions_run_id"), table_name="run_functions")
    op.drop_table("run_functions")
