"""Scanner results (feature E1).

Action-facing (auth: run token):
  POST /v1/runs/{id}/findings   what Bandit, pip-audit and detect-secrets found, and which
                                of it is new in this commit

Dashboard-facing (auth: Supabase sign-in, only the owner's runs):
  GET /v1/runs/{id}/findings    the same facts as the PR comment, in full

The scanners run in the user's runner; the server never runs them and never stores source code.
A scanner that could not run is stored as such and shown as "could not run", never as a pass.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import current_user
from app.core.run_token import current_run
from app.db.models.accounts import Repo, User
from app.db.models.findings import Finding, FindingAttempt
from app.db.models.runs import Run
from app.db.session import get_db
from app.services.findings import build_findings_view, clean, clean_path

router = APIRouter(tags=["findings"])

MAX_FINDINGS = 500
Tool = Literal["bandit", "pip-audit", "detect-secrets"]
Level = Literal["high", "medium", "low", "unknown"]


class ScannerIn(BaseModel):
    tool: Tool
    status: Literal["ran", "could_not_run"]
    reason: str | None = Field(default=None, max_length=1000)
    version: str | None = Field(default=None, max_length=40)
    # True when the old version of the code was scanned too, so "new" and "existing" mean
    # something. False means every finding of this scanner is "unknown".
    compared_with_base: bool = False


class FindingIn(BaseModel):
    tool: Tool
    rule_id: str = Field(max_length=200)
    severity: Level = "unknown"
    confidence: Level = "unknown"
    file: str | None = Field(default=None, max_length=1000)
    line: int | None = Field(default=None, ge=0, le=10_000_000)
    message: str = Field(max_length=5000)
    cwe: str | None = Field(default=None, max_length=40)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: Literal["new", "existing", "unknown"] = "unknown"


class FindingsIn(BaseModel):
    scanners: list[ScannerIn] = Field(default_factory=list, max_length=10)
    findings: list[FindingIn] = Field(default_factory=list, max_length=MAX_FINDINGS)
    # How many findings the runner left out because there were too many to send.
    omitted: int = Field(default=0, ge=0, le=10_000_000)


class FindingsOut(BaseModel):
    saved: int
    new: int
    existing: int
    unknown: int


@router.post("/v1/runs/{run_id}/findings", response_model=FindingsOut)
def post_findings(
    run_id: UUID,
    body: FindingsIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
):
    if run.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This run has already finished.")

    # Sending the list again replaces the earlier one, so a retry never doubles anything.
    db.execute(delete(Finding).where(Finding.run_id == run.id))
    # "New" and "existing" only mean something for a scanner that also scanned the old code.
    compared = {s.tool for s in body.scanners if s.compared_with_base}
    seen: set[str] = set()
    rows: list[Finding] = []
    for item in body.findings:
        if item.fingerprint in seen:
            continue
        seen.add(item.fingerprint)
        rows.append(
            Finding(
                run_id=run.id,
                tool=item.tool,
                rule_id=clean(item.rule_id, 60) or "unknown",
                severity=item.severity,
                confidence=item.confidence,
                file=clean_path(item.file),
                line=item.line,
                message=clean(item.message, 400) or "(no message)",
                cwe=(clean(item.cwe, 20) or None) if item.cwe else None,
                fingerprint=item.fingerprint,
                origin=item.origin if item.tool in compared else "unknown",
            )
        )
    db.add_all(rows)

    scanners = []
    for scanner in body.scanners:
        mine = [r.origin for r in rows if r.tool == scanner.tool]
        scanners.append(
            {
                "tool": scanner.tool,
                "status": scanner.status,
                "reason": clean(scanner.reason, 300) if scanner.reason else None,
                "version": clean(scanner.version, 40) if scanner.version else None,
                "compared_with_base": scanner.compared_with_base,
                "new": mine.count("new"),
                "existing": mine.count("existing"),
                "unknown": mine.count("unknown"),
            }
        )
    run.summary = {**(run.summary or {}), "scanners": scanners, "findings_omitted": body.omitted}
    db.commit()

    return FindingsOut(
        saved=len(rows),
        new=sum(r.origin == "new" for r in rows),
        existing=sum(r.origin == "existing" for r in rows),
        unknown=sum(r.origin == "unknown" for r in rows),
    )


@router.get("/v1/runs/{run_id}/findings")
def get_findings(
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return build_findings_view(run, db)


@router.get("/v1/runs/{run_id}/findings/{finding_id}/attempts")
def get_finding_attempts(
    run_id: UUID,
    finding_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The proof agent's tries for one finding (E3): the AI-written test and what happened when
    the runner ran it. All of it is untrusted text; the test is shown, never run, on the server."""
    finding = db.scalar(
        select(Finding)
        .join(Run, Run.id == Finding.run_id)
        .join(Repo, Repo.id == Run.repo_id)
        .where(Finding.id == finding_id, Run.id == run_id, Repo.owner_user_id == user.id)
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Finding not found.")
    rows = db.scalars(
        select(FindingAttempt)
        .where(FindingAttempt.finding_id == finding.id)
        .order_by(FindingAttempt.attempt)
    )
    return {
        "finding_id": str(finding.id),
        "proof": finding.proof_status,
        "proof_reason": finding.proof_reason,
        "attempts": [
            {
                "attempt": a.attempt,
                "test_code": a.test_code,
                "lint_ok": a.lint_ok,
                "lint_errors": a.lint_errors or [],
                "outcomes": a.outcomes or [],
                "output": a.output,
                "model": a.model,
            }
            for a in rows
        ],
    }
