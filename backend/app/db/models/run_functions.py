"""Feature D tables: one row per changed function in a run, and one per test outcome.

`run_functions` is filled when the Action starts a run (one row per changed function).
`test_results` holds what happened to each generated test: how it did on the old and the new
code, the label, and the diagnosis. Both cascade from `runs`, so deleting a repo or an account
removes them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.accounts import JsonType

# pending -> generated -> tested, or one of the "no result" endings. A "no result" ending is
# never shown as a pass.
FUNCTION_STATUSES = (
    "pending",
    "generated",
    "tested",
    "skipped",
    "could_not_generate",
    "could_not_test",
)


class RunFunction(Base):
    __tablename__ = "run_functions"
    __table_args__ = (
        UniqueConstraint("run_id", "file", "name", name="uq_run_functions_run_id_file_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    file: Mapped[str] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(200))
    change_type: Mapped[str] = mapped_column(String(12), default="modified")

    status: Mapped[str] = mapped_column(String(20), default="pending")
    # Short, safe explanation when status is a "no result" ending.
    reason: Mapped[str | None] = mapped_column(String(300))

    # The tests the AI wrote (data, never run on the server) and a hash of what they were
    # written from: (old code, new code, context pack). Same hash = same answer, so it can
    # be reused (D7).
    test_code: Mapped[str | None] = mapped_column(Text)
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)

    # Mutation check result (D6): {"score": 0.8, "killed": 4, "total": 5, ...}
    mutation: Mapped[dict | None] = mapped_column(JsonType)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FunctionTestResult(Base):
    __tablename__ = "test_results"
    __table_args__ = (Index("ix_test_results_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_function_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_functions.id", ondelete="CASCADE"), index=True
    )
    # Repeated from run_function so ownership checks need one join, not two.
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))

    test_name: Mapped[str] = mapped_column(String(300))
    # Which repair round produced this test (0 = the first version).
    attempt: Mapped[int] = mapped_column(Integer, default=0)

    # passed | failed | error | skipped, on the old code and on the new code.
    before_result: Mapped[str] = mapped_column(String(10))
    after_result: Mapped[str] = mapped_column(String(10))
    # CATCHES_CHANGE | TEST_WRONG_ON_BEFORE | NO_SIGNAL | ODD
    label: Mapped[str] = mapped_column(String(24))

    # Reruns of a failing test on the new code, to spot flaky tests.
    rerun_passes: Mapped[int] = mapped_column(Integer, default=0)
    rerun_fails: Mapped[int] = mapped_column(Integer, default=0)

    # REAL_BUG | BAD_TEST | FLAKY, only for tests that fail on the new code.
    diagnosis: Mapped[str | None] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(String(400))
    # Cleaned failure message from the new-code run (untrusted text, shown in code spans).
    message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
