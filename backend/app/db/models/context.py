"""Feature C tables: the per-repo code map (the "context").

Every table cascades from `repos`, so removing a repo or an account removes its map.
Rows hold facts about the code (names, signatures, first docstring lines, links), not source.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.accounts import JsonType

CONTEXT_STATUSES = ("empty", "building", "complete")


class ContextState(Base):
    """One row per repo: how complete and how fresh its map is."""

    __tablename__ = "context_state"

    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), primary_key=True
    )
    # empty | building | complete
    status: Mapped[str] = mapped_column(String(20), default="empty")
    # The run that is currently updating the map (only that run may upload or commit).
    building_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_sha: Mapped[str | None] = mapped_column(String(64))
    # Commit of the last COMPLETED build. This is what "freshness" is measured against.
    built_from_sha: Mapped[str | None] = mapped_column(String(64))
    # True when some files were left out (too many or too large). Never hidden from the user.
    partial: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    skipped_files: Mapped[int] = mapped_column(Integer, default=0)
    # {path: hash} the current build must end up with; uploads are checked against it.
    pending_manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    repo_facts: Mapped[dict] = mapped_column(JsonType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContextFile(Base):
    __tablename__ = "context_files"
    __table_args__ = (UniqueConstraint("repo_id", "path"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_error: Mapped[str | None] = mapped_column(String(200))
    symbols: Mapped[list] = mapped_column(JsonType, default=list)
    imports: Mapped[list] = mapped_column(JsonType, default=list)
    calls: Mapped[list] = mapped_column(JsonType, default=list)
    dynamic_calls: Mapped[int] = mapped_column(Integer, default=0)
    built_from_sha: Mapped[str] = mapped_column(String(64))
    # AI summary (step C6). `summary_hash` is the content hash it was written for; when it differs
    # from `content_hash` the summary is stale.
    summary: Mapped[str | None] = mapped_column(Text)
    summary_hash: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContextEdge(Base):
    """A link between two places in the code. Built by the server from the files' facts (C3)."""

    __tablename__ = "context_edges"
    __table_args__ = (
        Index("ix_context_edges_repo_id_src_path", "repo_id", "src_path"),
        Index("ix_context_edges_repo_id_dst_path", "repo_id", "dst_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    repo_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), index=True
    )
    src_path: Mapped[str] = mapped_column(String(500))
    src_symbol: Mapped[str] = mapped_column(String(300))
    # import | call
    kind: Mapped[str] = mapped_column(String(10))
    # The name as written in the code, for example "cart.add_item".
    target_name: Mapped[str] = mapped_column(String(300))
    dst_path: Mapped[str | None] = mapped_column(String(500))
    dst_symbol: Mapped[str | None] = mapped_column(String(300))
    # False means we could not find the target inside this repo. Never guessed.
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # resolved   points at one place in this repo
    # external   a Python built-in, the standard library or an installed package (not in the repo)
    # unresolved could not be pinned to one place (variable calls, ambiguous names, ...)
    status: Mapped[str] = mapped_column(String(12), default="unresolved", server_default="unresolved")
    line: Mapped[int | None] = mapped_column(Integer)
    built_from_sha: Mapped[str] = mapped_column(String(64))
