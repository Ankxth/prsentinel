"""baseline (empty starting point)

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""
from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No tables yet. The first feature that needs a table adds a migration after this one.
    pass


def downgrade() -> None:
    pass
