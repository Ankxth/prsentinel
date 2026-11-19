"""Dashboard run page data (auth: Supabase sign-in, only the owner's runs):

  GET /v1/runs/{id}/functions   per-function test results, the same facts as the PR comment

The generated test code is never sent. Everything from the repo is untrusted text.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.db.models.accounts import Repo, User
from app.db.models.runs import Run
from app.db.session import get_db
from app.services.run_view import build_run_view

router = APIRouter(tags=["run page"])


@router.get("/v1/runs/{run_id}/functions")
def run_functions_view(
    run_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    run = db.scalar(
        select(Run)
        .join(Repo, Repo.id == Run.repo_id)
        .where(Run.id == run_id, Repo.owner_user_id == user.id)
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return build_run_view(run, db)
