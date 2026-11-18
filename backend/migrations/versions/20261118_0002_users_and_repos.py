"""users and repos

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("github_login", sa.String(length=100), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=True),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("github_login", name=op.f("uq_users_github_login")),
        sa.UniqueConstraint("github_id", name=op.f("uq_users_github_id")),
    )
    op.create_table(
        "repos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("github_owner", sa.String(length=100), nullable=False),
        sa.Column("github_name", sa.String(length=100), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("llm_opt_in", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("settings", JsonType, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_repos_owner_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repos")),
        sa.UniqueConstraint("github_id", name=op.f("uq_repos_github_id")),
    )
    op.create_index(op.f("ix_repos_owner_user_id"), "repos", ["owner_user_id"], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        _enable_row_level_security()


def _enable_row_level_security() -> None:
    """Postgres only. With RLS on and no policy, outsiders (the public API key) see nothing.
    The two policies let a signed-in Supabase user read only their own rows.
    The policies need Supabase's auth.uid(); on a plain Postgres they are skipped."""
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE repos ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF to_regprocedure('auth.uid()') IS NOT NULL THEN
            CREATE POLICY users_select_own ON users FOR SELECT USING (id = auth.uid());
            CREATE POLICY repos_select_own ON repos FOR SELECT USING (owner_user_id = auth.uid());
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_repos_owner_user_id"), table_name="repos")
    op.drop_table("repos")
    op.drop_table("users")
