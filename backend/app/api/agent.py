"""Proof agent loop for command injection findings (feature E3).

Action-facing (auth: run token):
  POST /v1/runs/{id}/findings/{fingerprint}/agent/step

Back and forth, one round per call. The first call carries the function's source and no result;
the server asks the AI for a test and returns it. The runner checks and runs the test in the
user's own runner and calls again with what happened; the server either settles the finding or
asks the AI for a better test, up to 5 tries per finding and 3 findings per run.
The server never runs the test. AI trouble is HTTP 200 with action "done" and a plain reason.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.run_token import current_run
from app.db.models.accounts import Repo
from app.db.models.findings import Finding, FindingAttempt
from app.db.models.runs import Run
from app.db.session import get_db
from app.services import proof
from app.services.findings import clean
from app.services.llm import LLMError, get_llm
from app.services.llm_usage import AllowanceUsed, check_allowance, record

router = APIRouter(tags=["agent"])

_LLM_REASONS = {
    "not_configured": "The AI is not switched on for this server.",
    "rate_limited": "The AI service is busy right now.",
    "unavailable": "The AI service could not be reached.",
    "bad_response": "The AI service returned an unexpected answer.",
}


class FunctionIn(BaseModel):
    file: str = Field(max_length=500)
    name: str = Field(max_length=200)
    source: str = Field(min_length=1, max_length=proof.MAX_SOURCE_CHARS)
    import_line: str = Field(max_length=200)
    class_name: str | None = Field(default=None, max_length=200)


class ResultIn(BaseModel):
    attempt: int = Field(ge=1, le=proof.MAX_ATTEMPTS)
    lint_ok: bool
    lint_errors: list[str] = Field(default=[], max_length=10)
    outcomes: list[Literal["canary", "clean", "error", "timeout"]] = Field(
        default=[], max_length=proof.RUNS_PER_ATTEMPT
    )
    output: str = Field(default="", max_length=20000)


class StepIn(BaseModel):
    function: FunctionIn
    result: ResultIn | None = None


class StepOut(BaseModel):
    action: str  # run_test | done
    attempt: int | None = None
    test_code: str | None = None
    verdict: str | None = None  # a proof status when action is done
    reason: str | None = None


def _done(finding: Finding, status_: str, reason: str, model: str | None = None) -> StepOut:
    finding.proof_status = status_
    finding.proof_reason = clean(reason, 300)
    if model:
        finding.proof_model = model[:80]
    return StepOut(action="done", verdict=status_, reason=finding.proof_reason)


def _explain(db, repo, settings, complete, finding, function, test_code) -> None:
    """Optional two-sentence explanation after a verified result. Any trouble means no explanation;
    it never changes the verdict."""
    try:
        check_allowance(db, repo, settings)
        result = complete(
            proof.EXPLAIN_PURPOSE,
            proof.build_explain_messages(finding, function.model_dump(), test_code),
            budget_key="default",
            max_tokens=proof.EXPLAIN_MAX_TOKENS,
        )
    except (AllowanceUsed, LLMError):
        return
    record(db, repo, proof.EXPLAIN_PURPOSE, result)
    try:
        finding.proof_explanation = proof.parse_explanation(result.text)
    except ValueError:
        return


@router.post(
    "/v1/runs/{run_id}/findings/{fingerprint}/agent/step", response_model=StepOut
)
def agent_step(
    run_id: UUID,
    fingerprint: str,
    body: StepIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    complete=Depends(get_llm),
):
    if run.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This run has already finished.")
    if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown finding.")
    finding = db.scalar(
        select(Finding).where(Finding.run_id == run.id, Finding.fingerprint == fingerprint)
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown finding.")
    repo = db.get(Repo, run.repo_id)

    if finding.tool != "bandit" or finding.rule_id not in proof.RULES or finding.origin not in (
        "new", "unknown",
    ):
        return StepOut(action="done", verdict="not_attempted", reason="This kind of finding is not tried.")
    if not repo.llm_opt_in:
        return StepOut(
            action="done",
            verdict="not_attempted",
            reason="This repository has not allowed sending code to the AI (opt-in).",
        )
    if finding.proof_status in ("verified", "needs_review", "not_reproduced"):
        return StepOut(action="done", verdict=finding.proof_status, reason=finding.proof_reason)

    attempts = list(
        db.scalars(
            select(FindingAttempt)
            .where(FindingAttempt.finding_id == finding.id)
            .order_by(FindingAttempt.attempt)
        )
    )

    if body.result is not None:
        last = attempts[-1] if attempts else None
        if last is None or last.attempt != body.result.attempt:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown attempt.")
        last.lint_ok = body.result.lint_ok
        last.lint_errors = [clean(e, 200) for e in body.result.lint_errors]
        last.outcomes = list(body.result.outcomes)
        last.output = clean(body.result.output, proof.MAX_OUTPUT_CHARS) if body.result.output else None
        verdict, reason = proof.judge(
            body.result.lint_ok, list(body.result.outcomes), body.function.name
        )
        if verdict:
            out = _done(finding, verdict, reason, last.model)
            if verdict == "verified":
                _explain(db, repo, settings, complete, finding, body.function, last.test_code)
            db.commit()
            return out
        if len(attempts) >= proof.MAX_ATTEMPTS:
            out = _done(finding, "not_reproduced", proof.give_up_reason(len(attempts)), last.model)
            db.commit()
            return out
    elif attempts:
        # A first call again after a restart: continue from the stored tries.
        if len(attempts) >= proof.MAX_ATTEMPTS:
            out = _done(finding, "not_reproduced", proof.give_up_reason(len(attempts)))
            db.commit()
            return out
    else:
        tried = db.scalar(
            select(func.count(func.distinct(FindingAttempt.finding_id)))
            .join(Finding, Finding.id == FindingAttempt.finding_id)
            .where(Finding.run_id == run.id)
        )
        if (tried or 0) >= proof.MAX_FINDINGS:
            return StepOut(
                action="done",
                verdict="not_attempted",
                reason=f"At most {proof.MAX_FINDINGS} findings are tried per run.",
            )

    def stop(reason: str) -> StepOut:
        if attempts:
            out = _done(finding, "not_reproduced", reason + " Could not confirm.")
        else:
            out = StepOut(action="done", verdict="not_attempted", reason=reason)
        db.commit()
        return out

    try:
        check_allowance(db, repo, settings)
    except AllowanceUsed:
        return stop("The daily AI allowance has been used up.")
    history = [
        {
            "attempt": a.attempt,
            "test_code": a.test_code,
            "lint_ok": a.lint_ok,
            "lint_errors": a.lint_errors,
            "outcomes": a.outcomes,
            "output": a.output,
        }
        for a in attempts
    ]
    try:
        result = complete(
            proof.PURPOSE,
            proof.build_messages(finding, body.function.model_dump(), history),
            budget_key="default",
            max_tokens=proof.MAX_OUTPUT_TOKENS,
        )
    except LLMError as error:
        return stop(_LLM_REASONS.get(error.kind, _LLM_REASONS["unavailable"]))
    record(db, repo, proof.PURPOSE, result)
    try:
        code = proof.extract_test(result.text)
    except ValueError:
        return stop("The AI gave an unusable answer.")

    number = len(attempts) + 1
    db.add(
        FindingAttempt(
            finding_id=finding.id, attempt=number, test_code=code, model=result.model[:80]
        )
    )
    finding.proof_model = result.model[:80]
    db.commit()
    return StepOut(action="run_test", attempt=number, test_code=code)
