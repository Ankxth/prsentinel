"""Mutation check result (feature D6).

Action-facing (auth: run token):
  POST /v1/runs/{id}/mutation   how many deliberate faults the generated tests caught

The runner makes small faults in the OLD version of the changed function and runs the final tests
against each one. The server only stores the counts it is told and works out the score with a fixed
rule. The score is a lower bound (some faults change nothing), and the comment says so. "Not
measured" is never shown as a good score.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.run_token import current_run
from app.db.models.run_functions import RunFunction
from app.db.models.runs import Run
from app.db.session import get_db

router = APIRouter(tags=["mutation"])

MAX_SURVIVORS = 10


class MutationIn(BaseModel):
    file: str = Field(max_length=500)
    name: str = Field(max_length=200)
    status: Literal["measured", "not_measured"]
    reason: str | None = Field(default=None, max_length=300)
    found: int = Field(default=0, ge=0, le=100_000)
    capped: bool = False
    stopped_early: bool = False
    killed: int = Field(default=0, ge=0, le=1000)
    survived: int = Field(default=0, ge=0, le=1000)
    equivalent: int = Field(default=0, ge=0, le=1000)
    timed_out: int = Field(default=0, ge=0, le=1000)
    could_not_run: int = Field(default=0, ge=0, le=1000)
    survivors: list[str] = Field(default_factory=list, max_length=MAX_SURVIVORS)


class MutationOut(BaseModel):
    function_id: UUID
    status: str
    score: float | None = None


def _short(text: str) -> str:
    return " ".join(text.replace("\x00", "").split())[:60]


@router.post("/v1/runs/{run_id}/mutation", response_model=MutationOut)
def post_mutation(
    run_id: UUID,
    body: MutationIn,
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
    if row.status != "tested":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This function has no test results to check."
        )

    scored = body.killed + body.survived
    # Counts that do not add up, or nothing scored, are "not measured", never a score.
    if body.status == "measured" and scored > 0:
        score = round(body.killed / scored, 4)
        row.mutation = {
            "status": "measured",
            "score": score,
            "killed": body.killed,
            "survived": body.survived,
            "equivalent": body.equivalent,
            "timed_out": body.timed_out,
            "could_not_run": body.could_not_run,
            "found": body.found,
            "capped": body.capped,
            "stopped_early": body.stopped_early,
            "survivors": [_short(s) for s in body.survivors],
        }
        db.commit()
        return MutationOut(function_id=row.id, status="measured", score=score)

    row.mutation = {
        "status": "not_measured",
        "score": None,
        "reason": (body.reason or "The mutation check produced no score.")[:300],
    }
    db.commit()
    return MutationOut(function_id=row.id, status="not_measured")
