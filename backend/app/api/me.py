"""The signed-in user."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import current_user
from app.db.models.accounts import User

router = APIRouter(prefix="/v1", tags=["me"])


class MeOut(BaseModel):
    id: UUID
    github_login: str
    avatar_url: str | None
    is_admin: bool
    created_at: datetime


@router.get("/me", response_model=MeOut)
def read_me(user: User = Depends(current_user)) -> User:
    return user
