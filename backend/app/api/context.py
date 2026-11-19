"""Context map (feature C).

Action-facing (auth: the run token, for a run that is still running):
  POST /v1/runs/{id}/context/manifest   send {path: hash}; get back which files we need
  POST /v1/runs/{id}/context/files      send facts for those files (batches)
  POST /v1/runs/{id}/context/commit     finish the update
Dashboard-facing (auth: Supabase sign-in, owner only):
  GET  /v1/repos/{id}/context
  GET  /v1/repos/{id}/context/pack, /context/file
  POST /v1/repos/{id}/context/file/summary   write (or return) the AI summary of one file
"""

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.core.config import Settings, get_settings
from app.core.run_token import current_run
from app.db.models.accounts import Repo, User
from app.db.models.context import ContextEdge, ContextFile
from app.db.models.runs import Run
from app.db.session import get_db
from app.services.context_engine import pack as context_pack
from app.services.context_engine import store, summaries
from app.services.context_engine.store import ContextError
from app.services.llm import LLMError, get_llm
from app.services.llm_usage import (
    SUMMARY_PURPOSE,
    AllowanceUsed,
    check_summary_allowance,
    record,
)

router = APIRouter(tags=["context"])

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BAD_PATH = re.compile(r"[\x00-\x1f]")


def _check_path(path: str) -> str:
    if not path or len(path) > 500 or BAD_PATH.search(path):
        raise ValueError("invalid file path")
    return path


async def limit_body(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Refuse oversized uploads before the (heavy) body validation runs."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_context_upload_bytes:
        raise HTTPException(413, "Upload is too large.")
    if len(await request.body()) > settings.max_context_upload_bytes:
        raise HTTPException(413, "Upload is too large.")


def _raise(error: ContextError):
    raise HTTPException(status_code=error.status, detail=error.detail) from None


# ---------------------------------------------------------------- Action-facing

class ManifestIn(BaseModel):
    files: dict[str, str]
    total_files: int = Field(default=0, ge=0, le=1_000_000)
    skipped_files: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("files")
    @classmethod
    def _files(cls, value: dict[str, str]) -> dict[str, str]:
        for path, content_hash in value.items():
            _check_path(path)
            if not HASH_PATTERN.match(content_hash):
                raise ValueError("hashes must be 64 hex characters")
        return value


class ManifestOut(BaseModel):
    need: list[str]
    unchanged: int
    removed: int
    max_batch_files: int


@router.post("/v1/runs/{run_id}/context/manifest", response_model=ManifestOut)
def send_manifest(
    body: ManifestIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: None = Depends(limit_body),
):
    try:
        result = store.begin_build(
            db, run, body.files, body.total_files, body.skipped_files, settings.max_context_files
        )
    except ContextError as error:
        _raise(error)
    return ManifestOut(max_batch_files=settings.max_context_batch_files, **result)


class SymbolIn(BaseModel):
    name: str = Field(max_length=300)
    kind: str = Field(pattern="^(function|class)$")
    signature: str = Field(default="", max_length=300)
    doc: str = Field(default="", max_length=200)
    lines: list[int] = Field(min_length=2, max_length=2)
    decorators: list[str] = Field(default_factory=list, max_length=20)
    bases: list[str] = Field(default_factory=list, max_length=20)
    is_fixture: bool = False

    @field_validator("decorators", "bases")
    @classmethod
    def _short(cls, value: list[str]) -> list[str]:
        if any(len(item) > 200 for item in value):
            raise ValueError("name too long")
        return value


class ImportIn(BaseModel):
    module: str = Field(max_length=300)
    name: str | None = Field(default=None, max_length=200)
    asname: str | None = Field(default=None, max_length=200)
    level: int = Field(default=0, ge=0, le=20)


class CallIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    in_: str = Field(alias="in", max_length=300)
    name: str = Field(max_length=300)
    line: int = Field(default=0, ge=0)


class FileFactsIn(BaseModel):
    path: str
    hash: str
    is_test: bool = False
    parse_error: str | None = Field(default=None, max_length=200)
    symbols: list[SymbolIn] = Field(default_factory=list, max_length=3000)
    imports: list[ImportIn] = Field(default_factory=list, max_length=1000)
    calls: list[CallIn] = Field(default_factory=list, max_length=10000)
    dynamic_calls: int = Field(default=0, ge=0)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _check_path(value)

    @field_validator("hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        if not HASH_PATTERN.match(value):
            raise ValueError("hash must be 64 hex characters")
        return value


class FilesIn(BaseModel):
    files: list[FileFactsIn] = Field(min_length=1)


class FilesOut(BaseModel):
    saved: int


@router.post("/v1/runs/{run_id}/context/files", response_model=FilesOut)
def send_files(
    body: FilesIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: None = Depends(limit_body),
):
    if len(body.files) > settings.max_context_batch_files:
        raise HTTPException(
            status_code=422,
            detail=f"At most {settings.max_context_batch_files} files per request.",
        )
    paths = [f.path for f in body.files]
    if len(set(paths)) != len(paths):
        raise HTTPException(status_code=422, detail="The same file appears twice in one request.")
    items = [
        {**f.model_dump(exclude={"calls"}), "calls": [c.model_dump(by_alias=True) for c in f.calls]}
        for f in body.files
    ]
    try:
        saved = store.save_files(db, run, items)
    except ContextError as error:
        _raise(error)
    return FilesOut(saved=saved)


class CommitIn(BaseModel):
    # Hints only the runner can know (for example whether the repo has a pytest config file).
    pytest_config: bool = False


class CommitOut(BaseModel):
    status: str
    built_from_sha: str | None
    partial: bool
    python_files: int


@router.post("/v1/runs/{run_id}/context/commit", response_model=CommitOut)
def commit(
    body: CommitIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
):
    try:
        state = store.commit_build(db, run, {"pytest_config": body.pytest_config})
    except ContextError as error:
        _raise(error)
    return CommitOut(
        status=state.status,
        built_from_sha=state.built_from_sha,
        partial=state.partial,
        python_files=state.repo_facts.get("python_files", 0),
    )


# ---------------------------------------------------------------- dashboard-facing

class ContextFileOut(BaseModel):
    path: str
    is_test: bool
    parse_error: str | None
    symbols: int
    calls: int


class ContextOut(BaseModel):
    status: str
    built_from_sha: str | None
    latest_run_sha: str | None
    partial: bool
    total_files: int
    skipped_files: int
    repo_facts: dict
    file_count: int
    edges: dict[str, int]
    files: list[ContextFileOut]


@router.get("/v1/repos/{repo_id}/context", response_model=ContextOut)
def get_context(
    repo_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    repo = db.scalar(select(Repo).where(Repo.id == repo_id, Repo.owner_user_id == user.id))
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    state = store.get_state(db, repo.id)
    db.commit()
    rows = db.scalars(
        select(ContextFile).where(ContextFile.repo_id == repo.id).order_by(ContextFile.path).limit(1000)
    ).all()
    count = db.scalar(select(func.count()).select_from(ContextFile).where(ContextFile.repo_id == repo.id))
    latest = db.scalar(
        select(Run.sha).where(Run.repo_id == repo.id).order_by(Run.started_at.desc()).limit(1)
    )
    edge_counts = {"resolved": 0, "external": 0, "unresolved": 0}
    for edge_status, number in db.execute(
        select(ContextEdge.status, func.count())
        .where(ContextEdge.repo_id == repo.id, ContextEdge.kind == "call")
        .group_by(ContextEdge.status)
    ):
        edge_counts[edge_status] = number
    return ContextOut(
        status=state.status,
        built_from_sha=state.built_from_sha,
        latest_run_sha=latest,
        partial=state.partial,
        total_files=state.total_files,
        skipped_files=state.skipped_files,
        repo_facts=state.repo_facts or {},
        file_count=count or 0,
        edges=edge_counts,
        files=[
            ContextFileOut(
                path=row.path,
                is_test=row.is_test,
                parse_error=row.parse_error,
                symbols=len(row.symbols or []),
                calls=len(row.calls or []),
            )
            for row in rows
        ],
    )


class PackOut(BaseModel):
    found: bool
    pack: dict
    text: str


@router.get("/v1/repos/{repo_id}/context/pack", response_model=PackOut)
def get_pack(
    repo_id: UUID,
    file: str,
    function: str,
    budget: int = 3000,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """A preview of the context pack the test generator would receive for one function."""
    repo = db.scalar(select(Repo).where(Repo.id == repo_id, Repo.owner_user_id == user.id))
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    built = context_pack.get_context_pack(db, repo.id, file, function, budget)
    if not built.found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That function is not in the code map."
        )
    return PackOut(found=True, pack=built.to_dict(), text=built.render())


class SymbolOut(BaseModel):
    name: str
    kind: str
    signature: str
    doc: str
    lines: list[int]


class FileDetailOut(BaseModel):
    path: str
    is_test: bool
    parse_error: str | None
    symbols: list[SymbolOut]
    # AI summary (C6). `summary_current` is False when the file changed after it was written.
    summary: str | None = None
    summary_current: bool = False


@router.get("/v1/repos/{repo_id}/context/file", response_model=FileDetailOut)
def get_context_file(
    repo_id: UUID,
    path: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The functions and classes the code map holds for one file (names and signatures only)."""
    repo = db.scalar(select(Repo).where(Repo.id == repo_id, Repo.owner_user_id == user.id))
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    row = db.scalar(
        select(ContextFile).where(ContextFile.repo_id == repo.id, ContextFile.path == path)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not in the code map.")
    return FileDetailOut(
        path=row.path,
        is_test=row.is_test,
        parse_error=row.parse_error,
        symbols=[
            SymbolOut(
                name=s["name"], kind=s["kind"], signature=s.get("signature") or "",
                doc=s.get("doc") or "", lines=list(s["lines"]),
            )
            for s in (row.symbols or [])
        ],
        summary=row.summary,
        summary_current=summaries.is_current(row),
    )


class SummaryOut(BaseModel):
    path: str
    summary: str
    current: bool
    generated: bool


LLM_ERROR_STATUS = {"not_configured": 503, "rate_limited": 429}


@router.post("/v1/repos/{repo_id}/context/file/summary", response_model=SummaryOut)
def write_file_summary(
    repo_id: UUID,
    path: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    complete=Depends(get_llm),
):
    """Return the file's AI summary, writing it first if there is none or the file has changed.

    A POST because it can spend the AI allowance. Calling it again for an unchanged file is free.
    """
    repo = db.scalar(select(Repo).where(Repo.id == repo_id, Repo.owner_user_id == user.id))
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    if not repo.llm_opt_in:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI features are switched off for this repository.",
        )
    settings = get_settings()

    def metered(purpose, messages, **options):
        """The model call, paid from the summaries allowance. A summary that is already stored and
        current never gets here, so reading one stays free even when the allowance is used up."""
        check_summary_allowance(db, repo, settings)
        answer = complete(purpose, messages, **options)
        record(db, repo, SUMMARY_PURPOSE, answer)
        return answer

    try:
        result = summaries.get_file_summary(db, repo.id, path, metered)
    except AllowanceUsed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="No summary: today's allowance for file summaries has been used up.",
        ) from None
    except summaries.SummaryError as error:
        raise HTTPException(status_code=error.status, detail=error.detail) from None
    except LLMError as error:
        raise HTTPException(
            status_code=LLM_ERROR_STATUS.get(error.kind, 502), detail=str(error)
        ) from None
    return SummaryOut(
        path=result.path, summary=result.summary, current=result.current, generated=result.generated
    )
