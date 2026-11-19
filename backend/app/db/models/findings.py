"""Feature E table: what the scanners (Bandit, pip-audit, detect-secrets) found in one run.

The scanners run in the user's own Actions runner. The runner works out whether each finding is
new in this commit or already existed, and sends only a fingerprint (a hash that survives line
moves), the rule, the place and a short message. No source code is stored, and a leaked secret's
value is never sent at all. Rows cascade from `runs`, so deleting a repo or an account removes them.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TOOLS = ("bandit", "pip-audit", "detect-secrets")
SEVERITIES = ("high", "medium", "low", "unknown")
# new = not there before this commit, existing = already there, unknown = the old version could
# not be scanned, so nobody can tell.
ORIGINS = ("new", "existing", "unknown")


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("run_id", "fingerprint", name="uq_findings_run_id_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )

    tool: Mapped[str] = mapped_column(String(20))
    rule_id: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(10), default="unknown")
    confidence: Mapped[str] = mapped_column(String(10), default="unknown")
    file: Mapped[str | None] = mapped_column(String(500))
    line: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String(400))
    cwe: Mapped[str | None] = mapped_column(String(20))

    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    origin: Mapped[str] = mapped_column(String(10), default="unknown")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
