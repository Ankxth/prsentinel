"""Test results (feature D3).

Action-facing (auth: run token):
  POST /v1/runs/{id}/results   what happened when the runner ran the generated tests

The runner runs the tests; the server only labels what it is told, using fixed rules
(`app.services.classifier`). The AI never decides a label. Everything in the body came from the
user's repository and is untrusted: names and messages are cleaned and stored as text only.
"""

import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.run_token import current_run
from app.db.models.run_functions import FunctionTestResult, RunFunction
from app.db.models.runs import Run
from app.db.session import get_db
from app.services import classifier

router = APIRouter(tags=["results"])

MAX_TESTS = 200
MAX_MESSAGE_CHARS = 1500

# Anything that looks like a file path is replaced, so a message cannot carry folder names.
PATH_LIKE = re.compile(r"[A-Za-z]:[\\/][^\s'\"]*|/(?:[\w.\-]+/)*[\w.\-]+\.\w+")


def clean_message(text: str | None) -> str | None:
    if not text:
        return None
    text = PATH_LIKE.sub("<path>", text.replace("\x00", ""))
    return text[:MAX_MESSAGE_CHARS]


class TestOutcomeIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    before: str = Field(max_length=20)  # passed | failed | error | skipped (unknown = error)
    after: str = Field(max_length=20)
    rerun_passes: int = Field(default=0, ge=0, le=50)
    rerun_fails: int = Field(default=0, ge=0, le=50)
    message: str | None = Field(default=None, max_length=20_000)


class ResultsIn(BaseModel):
    file: str = Field(max_length=500)
    name: str = Field(max_length=200)
    status: Literal["tested", "could_not_test"]
    reason: str | None = Field(default=None, max_length=300)
    attempt: int = Field(default=0, ge=0, le=10)
    tests: list[TestOutcomeIn] = Field(default_factory=list, max_length=MAX_TESTS)


class TestOut(BaseModel):
    name: str
    label: str
    diagnosis: str | None
    reason: str | None


class ResultsOut(BaseModel):
    function_id: UUID
    status: str
    reason: str | None = None
    tests: list[TestOut] = Field(default_factory=list)


@router.post("/v1/runs/{run_id}/results", response_model=ResultsOut)
def post_results(
    run_id: UUID,
    body: ResultsIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
):
    if run.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This run has already finished.")
    row = db.scalar(
        select(RunFunction).where(
            RunFunction.run_id == run.id,
            RunFunction.file == body.file,
            RunFunction.name == body.name,
        )
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="That function is not one of this run's changes."
        )
    if not row.test_code:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="No tests were generated for this function."
        )

    if body.status == "could_not_test":
        row.status = "could_not_test"
        row.reason = (body.reason or "The tests could not be run.")[:300]
        db.commit()
        return ResultsOut(function_id=row.id, status=row.status, reason=row.reason)

    if not body.tests:
        # Zero results is "no result", never a pass.
        row.status = "could_not_test"
        row.reason = "The tests produced no results."
        db.commit()
        return ResultsOut(function_id=row.id, status=row.status, reason=row.reason)

    # Sending the same attempt again replaces it instead of doubling it.
    db.execute(
        delete(FunctionTestResult).where(
            FunctionTestResult.run_function_id == row.id,
            FunctionTestResult.attempt == body.attempt,
        )
    )
    is_new = row.change_type == "added"
    out = []
    seen = set()
    for item in body.tests:
        if item.name in seen:
            continue
        seen.add(item.name)
        before = classifier.SKIPPED if is_new else classifier.normalize_result(item.before)
        after = classifier.normalize_result(item.after)
        label = classifier.NO_SIGNAL if is_new else classifier.label_for(before, after)
        diagnosis = reason = None
        if classifier.fails_on_new_code(after):
            if is_new:
                verdict = classifier.classify_new_function(after, item.rerun_passes, item.rerun_fails)
            else:
                verdict = classifier.rule_classify(before, after, item.rerun_passes, item.rerun_fails)
            diagnosis, reason = verdict["diagnosis"], verdict["reason"]
        db.add(
            FunctionTestResult(
                run_function_id=row.id,
                run_id=run.id,
                test_name=item.name,
                attempt=body.attempt,
                before_result=before,
                after_result=after,
                label=label,
                rerun_passes=item.rerun_passes,
                rerun_fails=item.rerun_fails,
                diagnosis=diagnosis,
                reason=reason,
                message=clean_message(item.message) if diagnosis else None,
            )
        )
        out.append(TestOut(name=item.name, label=label, diagnosis=diagnosis, reason=reason))
    row.status, row.reason = "tested", None
    db.commit()
    return ResultsOut(function_id=row.id, status="tested", tests=out)
