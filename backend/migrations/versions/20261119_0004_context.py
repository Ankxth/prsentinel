"""context: per-repo code map (state, files, edges)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TABLES = ("context_state", "context_files", "context_edges")


def upgrade() -> None:
    op.create_table(
        "context_state",
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("building_run_id", sa.Uuid(), nullable=True),
        sa.Column("target_sha", sa.String(length=64), nullable=True),
        sa.Column("built_from_sha", sa.String(length=64), nullable=True),
        sa.Column("partial", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("total_files", sa.Integer(), nullable=False),
        sa.Column("skipped_files", sa.Integer(), nullable=False),
        sa.Column("pending_manifest", JsonType, nullable=False),
        sa.Column("repo_facts", JsonType, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repos.id"],
            name=op.f("fk_context_state_repo_id_repos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("repo_id", name=op.f("pk_context_state")),
    )

    op.create_table(
        "context_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False),
        sa.Column("parse_error", sa.String(length=200), nullable=True),
        sa.Column("symbols", JsonType, nullable=False),
        sa.Column("imports", JsonType, nullable=False),
        sa.Column("calls", JsonType, nullable=False),
        sa.Column("dynamic_calls", sa.Integer(), nullable=False),
        sa.Column("built_from_sha", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repos.id"],
            name=op.f("fk_context_files_repo_id_repos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_context_files")),
        sa.UniqueConstraint("repo_id", "path", name=op.f("uq_context_files_repo_id")),
    )
    op.create_index(op.f("ix_context_files_repo_id"), "context_files", ["repo_id"], unique=False)

    op.create_table(
        "context_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repo_id", sa.Uuid(), nullable=False),
        sa.Column("src_path", sa.String(length=500), nullable=False),
        sa.Column("src_symbol", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("target_name", sa.String(length=300), nullable=False),
        sa.Column("dst_path", sa.String(length=500), nullable=True),
        sa.Column("dst_symbol", sa.String(length=300), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("built_from_sha", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repos.id"],
            name=op.f("fk_context_edges_repo_id_repos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_context_edges")),
    )
    op.create_index(op.f("ix_context_edges_repo_id"), "context_edges", ["repo_id"], unique=False)
    op.create_index("ix_context_edges_repo_id_src_path", "context_edges", ["repo_id", "src_path"])
    op.create_index("ix_context_edges_repo_id_dst_path", "context_edges", ["repo_id", "dst_path"])

    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(
                f"""
                DO $$
                BEGIN
                  IF to_regprocedure('auth.uid()') IS NOT NULL THEN
                    CREATE POLICY {table}_select_own ON {table} FOR SELECT USING (
                      repo_id IN (SELECT id FROM repos WHERE owner_user_id = auth.uid())
                    );
                  END IF;
                END $$
                """
            )


def downgrade() -> None:
    op.drop_index("ix_context_edges_repo_id_dst_path", table_name="context_edges")
    op.drop_index("ix_context_edges_repo_id_src_path", table_name="context_edges")
    op.drop_index(op.f("ix_context_edges_repo_id"), table_name="context_edges")
    op.drop_table("context_edges")
    op.drop_index(op.f("ix_context_files_repo_id"), table_name="context_files")
    op.drop_table("context_files")
    op.drop_table("context_state")
