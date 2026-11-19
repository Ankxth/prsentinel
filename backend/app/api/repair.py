"""Test repair (feature D5).

Action-facing (auth: run token):
  POST /v1/runs/{id}/repair   fix tests that failed on the OLD code too

A test that fails on both the old and the new code is most likely a wrong test. The runner sends
those tests with their failure text; the server asks the AI for a fixed test file and hands it back.
The server never runs it. Like `/generate`, any AI-side problem is HTTP 200 with
`status: could_not_generate` and a plain reason. A failed repair leaves the first results of the
function untouched: it never turns them into "no result" and never into a pass.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.results import clean_message
from app.core.config import Settings, get_settings
from app.core.run_token import current_run
from app.db.models.accounts import Repo
from app.db.models.run_functions import RunFunction
from app.db.models.runs import Run
from app.db.session import get_db
from app.services import generator
from app.services.llm import LLMError, get_llm
from app.services.llm_usage import AllowanceUsed, check_allowance, record

router = APIRouter(tags=["repair"])

PURPOSE = "test_repair"

_LLM_REASONS = {
    "not_configured": "AI test repair is not switched on for this server.",
    "rate_limited": "The AI service is busy right now. The tests were not repaired.",
    "unavailable": "The AI service could not be reached. The tests were not repaired.",
    "bad_response": "The AI service returned an unexpected answer. The tests were not repaired.",
}


class FailingTestIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    before: str = Field(max_length=20)
    after: str = Field(max_length=20)
    message: str | None = Field(default=None, max_length=20_000)


class RepairIn(BaseModel):
    file: str = Field(max_length=500)
    name: str = Field(max_length=200)
    # The round being asked for: 1 is the first repair of the first tests (attempt 0).
    attempt: int = Field(ge=1, le=generator.MAX_REPAIR_ROUNDS)
    old_code: str = Field(min_length=1, max_length=generator.MAX_CODE_CHARS)
    new_code: str = Field(min_length=1, max_length=generator.MAX_CODE_CHARS)
    # The tests as they are now (the runner has them; the server does not keep repaired files).
    test_code: str = Field(min_length=1, max_length=generator.MAX_TEST_CHARS)
    failing: list[FailingTestIn] = Field(min_length=1, max_length=generator.MAX_FAILING)


class RepairOut(BaseModel):
    function_id: UUID
    status: str  # generated | could_not_generate
    reason: str | None = None
    test_code: str | None = None
    attempt: int
    import_line: str


@router.post("/v1/runs/{run_id}/repair", response_model=RepairOut)
def repair_tests(
    run_id: UUID,
    body: RepairIn,
    run: Run = Depends(current_run),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    complete=Depends(get_llm),
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
    if row.status != "tested" or row.change_type != "modified":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Only a modified function whose tests were run can have them repaired.",
        )

    line = generator.import_line(body.file, body.name)

    def no_result(reason: str) -> RepairOut:
        # The function keeps its earlier results; only this repair did not happen.
        return RepairOut(
            function_id=row.id, status="could_not_generate", reason=reason[:300],
            attempt=body.attempt, import_line=line,
        )

    repo = db.get(Repo, run.repo_id)
    if not repo.llm_opt_in:
        return no_result("This repository has not allowed sending code to the AI (opt-in).")
    try:
        check_allowance(db, repo, settings)
    except AllowanceUsed:
        return no_result("The daily AI allowance has been used up, so the tests were not repaired.")

    failing = [
        {"name": t.name, "before": t.before, "after": t.after, "message": clean_message(t.message)}
        for t in body.failing
    ]
    messages = generator.build_repair_messages(
        body.file, body.name, body.old_code, body.new_code, body.test_code, failing
    )
    try:
        result = complete(
            PURPOSE, messages, budget_key="default", max_tokens=generator.MAX_OUTPUT_TOKENS
        )
    except LLMError as error:
        return no_result(_LLM_REASONS.get(error.kind, _LLM_REASONS["unavailable"]))
    record(db, repo, PURPOSE, result)

    try:
        code = generator.extract_code(result.text)
        generator.check_repaired(code, line)
    except ValueError:
        return no_result("The AI did not return usable repaired tests.")
    return RepairOut(
        function_id=row.id, status="generated", test_code=code, attempt=body.attempt,
        import_line=line,
    )
