"""Feature E table: what the scanners (Bandit, pip-audit, detect-secrets) found in one run.

The scanners run in the user's own Actions runner. The runner works out whether each finding is
new in this commit or already existed, and sends only a fingerprint (a hash that survives line
moves), the rule, the place and a short message. No source code is stored, and a leaked secret's
value is never sent at all. Rows cascade from `runs`, so deleting a repo or an account removes them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
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

    # AI opinion (E2). Only a hint: it never changes `origin` and never hides a finding.
    # triage is "likely_real" | "likely_false_positive" | "unsure", or None when nobody judged it.
    triage: Mapped[str | None] = mapped_column(String(24))
    triage_reason: Mapped[str | None] = mapped_column(String(300))
    triage_model: Mapped[str | None] = mapped_column(String(80))

    # Proof agent (E3). proof_status is "verified" | "not_reproduced" | "needs_review" |
    # "not_attempted", or None when the agent was never asked. "not_reproduced" means "could not
    # confirm", never "safe".
    proof_status: Mapped[str | None] = mapped_column(String(20))
    proof_reason: Mapped[str | None] = mapped_column(String(300))
    proof_model: Mapped[str | None] = mapped_column(String(80))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FindingAttempt(Base):
    """One try of the proof agent: the test it wrote and what happened when the runner ran it.

    The test is AI-written text (never run on the server). The failure output is cut short and is
    untrusted. Rows cascade from `findings`, so they go with the run, the repo and the account.
    """

    __tablename__ = "finding_attempts"
    __table_args__ = (
        UniqueConstraint("finding_id", "attempt", name="uq_finding_attempts_finding_id_attempt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    test_code: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(80))

    # Filled in when the runner reports back; all None while the test has not been run yet.
    lint_ok: Mapped[bool | None] = mapped_column(Boolean)
    lint_errors: Mapped[list | None] = mapped_column(JSON)
    outcomes: Mapped[list | None] = mapped_column(JSON)
    output: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
