"""Daily token allowance (feature F, first version).

Counts the tokens recorded in `llm_usage` since 00:00 UTC, per repo and per owner. Callers check
before a model call and record after it. When an allowance is used up the feature says so
("no result, allowance used"); it never pretends nothing was wrong.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.accounts import Repo
from app.db.models.llm_usage import LLMUsage


class AllowanceUsed(Exception):
    """`scope` is "repo" or "user"."""

    def __init__(self, scope: str):
        super().__init__(f"daily allowance used ({scope})")
        self.scope = scope


def _day_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _used(db: Session, *conditions) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens), 0)).where(
            LLMUsage.created_at >= _day_start(), *conditions
        )
    )
    return int(total or 0)


def used_today(db: Session, repo: Repo) -> dict:
    return {
        "repo": _used(db, LLMUsage.repo_id == repo.id),
        "user": _used(db, LLMUsage.user_id == repo.owner_user_id),
    }


def check_allowance(db: Session, repo: Repo, settings: Settings) -> None:
    used = used_today(db, repo)
    if used["repo"] >= settings.llm_daily_tokens_per_repo:
        raise AllowanceUsed("repo")
    if used["user"] >= settings.llm_daily_tokens_per_user:
        raise AllowanceUsed("user")


def record(db: Session, repo: Repo, purpose: str, result) -> None:
    db.add(
        LLMUsage(
            repo_id=repo.id,
            user_id=repo.owner_user_id,
            purpose=purpose,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
    )
    db.commit()
