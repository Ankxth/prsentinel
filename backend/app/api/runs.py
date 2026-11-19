"""Runs.

Action-facing (auth: GitHub identity token, then the run token):
  POST /v1/runs               start a run
  POST /v1/runs/{id}/finish   end a run and get the PR comment text
Dashboard-facing (auth: Supabase sign-in, only the owner's runs):
  GET  /v1/repos/{id}/runs
  GET  /v1/runs/{id}
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contract import CONTRACT_VERSION
from app.core.auth import current_user
from app.core.config import Settings, get_settings
from app.core.oidc import GitHubClaims, current_repo, github_claims
from app.core.run_token import create_run_token, current_run
from app.db.models.accounts import Repo, User
from app.db.models.runs import Run
from app.db.session import get_db
from app.services.comment import COMMENT_MARKER, build_comment
from app.services.run_functions import register_functions

router = APIRouter(tags=["runs"])

ALLOWED_EVENTS = {"push", "pull_request", "workflow_dispatch"}


# ---------------------------------------------------------------- Action-facing

class ChangedFunction(BaseModel):
    file: str = Field(max_length=500)
    name: str = Field(max_length=200)
    change_type: Literal["added", "modified", "deleted"] = "modified"


class StartRunIn(BaseModel):
    contract_version: int
    changed_files: int = Field(default=0, ge=0, le=100_000)
    changed_functions: list[ChangedFunction] = Field(default_factory=list, max_length=500)


class Limits(BaseModel):
    max_functions: int


class StartRunOut(BaseModel):
    contract_version: int
    run_id: UUID
    run_token: str
    expires_in: int
    repo: str
    limits: Limits


@router.post("/v1/runs", response_model=StartRunOut)
def start_run(
    body: StartRunIn,
    response: Response,
    claims: GitHubClaims = Depends(github_claims),
    repo: Repo = Depends(current_repo),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if body.contract_version != CONTRACT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This server speaks contract version {CONTRACT_VERSION}, "
                f"the Action sent {body.contract_version}. Update the Action."
            ),
        )
    if claims.event_name not in ALLOWED_EVENTS:
        raise HTTPException(
            status_code=422,
            detail=f"Events of type '{claims.event_name}' are not analysed.",
        )

    run = db.scalar(
        select(Run).where(
            Run.repo_id == repo.id,
            Run.github_run_id == claims.run_id,
            Run.run_attempt == claims.run_attempt,
        )
    )
    created = run is None
    if created:
        run = Run(
            repo_id=repo.id,
            github_run_id=claims.run_id,
            run_attempt=claims.run_attempt,
            sha=claims.sha,
            ref=claims.ref,
            event=claims.event_name,
            status="running",
            summary={
                "changed_files": body.changed_files,
                "changed_functions": [f.model_dump() for f in body.changed_functions],
            },
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        register_functions(db, run, [f.model_dump() for f in body.changed_functions])
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK

    return StartRunOut(
        contract_version=CONTRACT_VERSION,
        run_id=run.id,
        run_token=create_run_token(run.id, repo.id, settings),
        expires_in=settings.run_token_ttl_seconds,
        repo=repo.full_name,
        limits=Limits(max_functions=settings.max_functions_per_run),
    )


class FinishIn(BaseModel):
    status: Literal["completed", "failed", "could_not_run"]
    reason: str | None = Field(default=None, max_length=300)


class FinishOut(BaseModel):
    status: str
    comment_marker: str
    comment_markdown: str


@router.post("/v1/runs/{run_id}/finish", response_model=FinishOut)
def finish_run(
    body: FinishIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
):
    if run.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This run has already finished."
        )
    run.status = body.status
    run.finished_at = datetime.now(timezone.utc)
    if body.reason:
        run.summary = {**(run.summary or {}), "reason": body.reason}
    db.commit()

    repo = db.get(Repo, run.repo_id)
    return FinishOut(
        status=run.status,
        comment_marker=COMMENT_MARKER,
        comment_markdown=build_comment(run, repo.full_name, db),
    )


# -------------------------------------------------------------- dashboard-facing

class RunOut(BaseModel):
    id: UUID
    repo_id: UUID
    sha: str
    ref: str
    event: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    summary: dict


@router.get("/v1/repos/{repo_id}/runs", response_model=list[RunOut])
def list_runs(
    repo_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    repo = db.scalar(select(Repo).where(Repo.id == repo_id, Repo.owner_user_id == user.id))
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    return db.scalars(
        select(Run).where(Run.repo_id == repo.id).order_by(Run.started_at.desc()).limit(50)
    ).all()


@router.get("/v1/runs/{run_id}", response_model=RunOut)
def get_run(
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
    return run
