"""Feature A tables: users and repos.

Both tables use Row Level Security in Postgres (see the migration), so a signed-in
user can only read their own rows. The backend connects with a role that bypasses
RLS, so every backend query on these tables must still filter by owner explicitly.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Uuid,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# JSON on every database, JSONB (indexable, faster) on Postgres.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"

    # Same value as the Supabase auth user id, so a login token maps straight to a row.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    github_login: Mapped[str] = mapped_column(String(100), unique=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    repos: Mapped[list["Repo"]] = relationship(
        back_populates="owner_user", cascade="all, delete-orphan", passive_deletes=True
    )


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # GitHub's own numeric id. Unique: one repo belongs to one PRSentinel user,
    # which is how an Action run is matched to its owner.
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    github_owner: Mapped[str] = mapped_column(String(100))
    github_name: Mapped[str] = mapped_column(String(100))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    # The user has agreed that code is sent to Groq/Gemini. Nothing runs without it.
    llm_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    settings: Mapped[dict] = mapped_column(JsonType, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    owner_user: Mapped[User] = relationship(back_populates="repos")

    @property
    def full_name(self) -> str:
        return f"{self.github_owner}/{self.github_name}"
