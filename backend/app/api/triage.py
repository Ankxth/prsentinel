"""AI opinion on scanner findings (feature E2).

Action-facing (auth: run token):
  POST /v1/runs/{id}/triage   short code snippets for some of this run's new Bandit findings

The runner sends, for each finding it wants judged, the fingerprint it already reported and about
ten lines of code around it. The server asks the AI for one opinion per finding and stores only
the verdict and a one-sentence reason. The snippet is used for the model call and not stored.

Anything that goes wrong on the AI side (no opt-in, allowance used, model trouble) is an HTTP 200
with status "not_triaged" and a plain reason, never an error that stops the run. A finding that
was not judged simply has no opinion; it is never hidden and never called harmless.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.run_token import current_run
from app.db.models.accounts import Repo
from app.db.models.findings import Finding
from app.db.models.runs import Run
from app.db.session import get_db
from app.services import triage
from app.services.llm import LLMError, get_llm
from app.services.llm_usage import AllowanceUsed, check_allowance, record

router = APIRouter(tags=["triage"])

_LLM_REASONS = {
    "not_configured": "AI opinions are not switched on for this server.",
    "rate_limited": "The AI service is busy right now, so some findings have no AI opinion.",
    "unavailable": "The AI service could not be reached, so some findings have no AI opinion.",
    "bad_response": "The AI service returned an unexpected answer, so some findings have no AI opinion.",
}


class TriageItem(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    snippet: str = Field(min_length=1, max_length=triage.MAX_SNIPPET_CHARS)


class TriageIn(BaseModel):
    items: list[TriageItem] = Field(max_length=triage.MAX_ITEMS)


class TriageResult(BaseModel):
    fingerprint: str
    verdict: str
    reason: str


class TriageOut(BaseModel):
    status: str  # triaged | not_triaged
    reason: str | None = None
    results: list[TriageResult] = []
    skipped: int = 0


@router.post("/v1/runs/{run_id}/triage", response_model=TriageOut)
def post_triage(
    run_id: UUID,
    body: TriageIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    complete=Depends(get_llm),
):
    if run.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This run has already finished.")
    repo = db.get(Repo, run.repo_id)
    if not repo.llm_opt_in:
        return TriageOut(
            status="not_triaged",
            reason="This repository has not allowed sending code to the AI (opt-in).",
        )

    results: list[TriageResult] = []
    skipped = 0
    stop_reason: str | None = None
    for item in body.items:
        finding = db.scalar(
            select(Finding).where(
                Finding.run_id == run.id, Finding.fingerprint == item.fingerprint
            )
        )
        # Only new (or not comparable) findings of the judged tools are looked at.
        if (
            finding is None
            or finding.tool not in triage.TRIAGE_TOOLS
            or finding.rule_id in triage.SKIP_RULES
            or finding.origin not in ("new", "unknown")
        ):
            skipped += 1
            continue
        try:
            check_allowance(db, repo, settings)
        except AllowanceUsed:
            stop_reason = "The daily AI allowance has been used up, so some findings have no AI opinion."
            break
        try:
            result = complete(
                triage.PURPOSE,
                triage.build_messages(finding, item.snippet),
                budget_key="default",
                max_tokens=triage.MAX_OUTPUT_TOKENS,
            )
        except LLMError as error:
            stop_reason = _LLM_REASONS.get(error.kind, _LLM_REASONS["unavailable"])
            break  # do not hammer a service that is failing
        record(db, repo, triage.PURPOSE, result)
        try:
            verdict, reason = triage.parse_reply(result.text)
        except ValueError:
            skipped += 1
            stop_reason = stop_reason or "The AI gave an unusable answer for some findings."
            continue
        finding.triage, finding.triage_reason, finding.triage_model = (
            verdict, reason, result.model[:80],
        )
        results.append(TriageResult(fingerprint=finding.fingerprint, verdict=verdict, reason=reason))
    db.commit()

    return TriageOut(
        status="triaged" if results else "not_triaged",
        reason=stop_reason if not results or stop_reason else None,
        results=results,
        skipped=skipped,
    )
