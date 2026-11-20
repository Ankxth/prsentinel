"""findings: AI opinion columns (feature E2)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("triage", sa.String(length=24), nullable=True))
    op.add_column("findings", sa.Column("triage_reason", sa.String(length=300), nullable=True))
    op.add_column("findings", sa.Column("triage_model", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("findings", "triage_model")
    op.drop_column("findings", "triage_reason")
    op.drop_column("findings", "triage")
