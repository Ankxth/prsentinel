"""The signed-in user."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.db.models.accounts import User
from app.db.session import get_db
from app.services.supabase_admin import get_auth_user_deleter

router = APIRouter(prefix="/v1", tags=["me"])


class MeOut(BaseModel):
    id: UUID
    github_login: str
    avatar_url: str | None
    is_admin: bool
    created_at: datetime


class DeleteMeOut(BaseModel):
    data_deleted: bool
    login_removed: bool


@router.get("/me", response_model=MeOut)
def read_me(user: User = Depends(current_user)) -> User:
    return user


@router.delete("/me", response_model=DeleteMeOut)
def delete_me(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    delete_login=Depends(get_auth_user_deleter),
) -> DeleteMeOut:
    """Delete the user and everything that belongs to them.

    Every table that holds user data must cascade from `users` (directly, or through
    `repos`), so deleting the user row removes it all. Add a test when you add a table.
    """
    user_id = user.id
    db.delete(user)
    db.commit()
    return DeleteMeOut(data_deleted=True, login_removed=delete_login(user_id))
