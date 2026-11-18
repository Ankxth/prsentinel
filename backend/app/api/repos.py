"""Add, list and remove repositories. Every query is limited to the signed-in user."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.core.config import Settings, get_settings
from app.db.models.accounts import Repo, User
from app.db.session import get_db
from app.services.github import RepoLookupError, get_repo_lookup, parse_full_name

router = APIRouter(prefix="/v1/repos", tags=["repos"])

LOOKUP_ERROR_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "rate_limited": status.HTTP_503_SERVICE_UNAVAILABLE,
    "unavailable": status.HTTP_502_BAD_GATEWAY,
}


class RepoIn(BaseModel):
    full_name: str
    llm_opt_in: bool

    @field_validator("full_name")
    @classmethod
    def check_full_name(cls, value: str) -> str:
        parse_full_name(value)  # raises ValueError with a readable message
        return value

    @field_validator("llm_opt_in")
    @classmethod
    def must_agree(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "You must agree that the repository's code is sent to the AI providers "
                "(Groq, Gemini) to add it."
            )
        return value


class RepoOut(BaseModel):
    id: UUID
    full_name: str
    github_id: int
    default_branch: str
    is_public: bool
    llm_opt_in: bool
    created_at: datetime


def _own_repo_or_404(db: Session, user: User, repo_id: UUID) -> Repo:
    repo = db.scalar(
        select(Repo).where(Repo.id == repo_id, Repo.owner_user_id == user.id)
    )
    if repo is None:  # same answer whether it is missing or someone else's
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    return repo


@router.get("", response_model=list[RepoOut])
def list_repos(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Repo).where(Repo.owner_user_id == user.id).order_by(Repo.created_at)
    ).all()


@router.post("", response_model=RepoOut, status_code=status.HTTP_201_CREATED)
def add_repo(
    body: RepoIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    lookup=Depends(get_repo_lookup),
):
    count = db.scalar(select(func.count()).select_from(Repo).where(Repo.owner_user_id == user.id))
    if count >= settings.max_repos_per_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Repository limit reached ({settings.max_repos_per_user}). Remove one first.",
        )

    owner, name = parse_full_name(body.full_name)
    try:
        found = lookup(owner, name)
    except RepoLookupError as error:
        raise HTTPException(
            status_code=LOOKUP_ERROR_STATUS[error.kind], detail=str(error)
        ) from None

    if found.is_private:
        raise HTTPException(
            status_code=422,
            detail="Only public repositories are supported.",
        )

    existing = db.scalar(select(Repo).where(Repo.github_id == found.github_id))
    if existing is not None:
        detail = (
            "You already added this repository."
            if existing.owner_user_id == user.id
            else "This repository is already connected to another account."
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    repo = Repo(
        owner_user_id=user.id,
        github_id=found.github_id,
        github_owner=found.owner,
        github_name=found.name,
        default_branch=found.default_branch,
        is_public=True,
        llm_opt_in=True,
        settings={},
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


@router.get("/{repo_id}", response_model=RepoOut)
def get_repo(repo_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _own_repo_or_404(db, user, repo_id)


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_repo(repo_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    repo = _own_repo_or_404(db, user, repo_id)
    db.delete(repo)
    db.commit()
