"""Feature B table: one row per Action run (one workflow job run on one commit)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.accounts import JsonType

RUN_STATUSES = ("running", "completed", "failed", "could_not_run")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint(
            "repo_id", "github_run_id", "run_attempt", name="uq_runs_repo_id_github_run_id_run_attempt"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), index=True
    )

    # Taken from GitHub's signed identity token, never from the request body.
    github_run_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(Integer, default=1)
    sha: Mapped[str] = mapped_column(String(64))
    ref: Mapped[str] = mapped_column(String(255))
    event: Mapped[str] = mapped_column(String(50))

    # running | completed | failed | could_not_run
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # What the Action reported: changed files/functions, reason for could_not_run, and later
    # whatever features D and E add. Keep it small; big data gets its own table.
    summary: Mapped[dict] = mapped_column(JsonType, default=dict)

