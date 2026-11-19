"""Test generation (feature D2).

Action-facing (auth: run token):
  POST /v1/runs/{id}/generate   write pytest tests for one changed function

The server never runs the tests; it only asks the AI and hands the text back. Anything that goes
wrong on the AI side (no opt-in, allowance used, model trouble) is an HTTP 200 with
status "could_not_generate" and a plain reason, so the runner reports "no result" and does not
retry blindly. It is never turned into a pass.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.run_token import current_run
from app.db.models.accounts import Repo
from app.db.models.run_functions import RunFunction
from app.db.models.runs import Run
from app.db.session import get_db
from app.services import generator
from app.services.context_engine.pack import get_context_pack
from app.services.llm import LLMError, get_llm
from app.services.llm_usage import AllowanceUsed, check_allowance, record

router = APIRouter(tags=["generate"])

PURPOSE = "test_generation"

_LLM_REASONS = {
    "not_configured": "AI test generation is not switched on for this server.",
    "rate_limited": "The AI service is busy right now. No result for this function.",
    "unavailable": "The AI service could not be reached. No result for this function.",
    "bad_response": "The AI service returned an unexpected answer. No result for this function.",
}


class GenerateIn(BaseModel):
    file: str = Field(max_length=500)
    name: str = Field(max_length=200)
    # The runner sends the function's source (the server never stores source bodies).
    old_code: str | None = Field(default=None, max_length=generator.MAX_CODE_CHARS)
    new_code: str = Field(min_length=1, max_length=generator.MAX_CODE_CHARS)


class GenerateOut(BaseModel):
    function_id: UUID
    status: str  # generated | could_not_generate
    reason: str | None = None
    test_code: str | None = None
    cached: bool = False
    import_line: str


def _fail(db: Session, row: RunFunction, reason: str, import_line: str) -> GenerateOut:
    row.status = "could_not_generate"
    row.reason = reason[:300]
    db.commit()
    return GenerateOut(
        function_id=row.id, status=row.status, reason=row.reason, import_line=import_line
    )


@router.post("/v1/runs/{run_id}/generate", response_model=GenerateOut)
def generate_tests(
    run_id: UUID,
    body: GenerateIn,
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
    if row.change_type == "deleted":
        raise HTTPException(
            422, detail="A deleted function has nothing to test."
        )

    line = generator.import_line(body.file, body.name)
    repo = db.get(Repo, run.repo_id)
    if not repo.llm_opt_in:
        return _fail(
            db, row, "This repository has not allowed sending code to the AI (opt-in).", line
        )

    pack = get_context_pack(db, repo.id, body.file, body.name)
    pack_text = pack.render() if pack.found else ""
    model = settings.groq_model
    key = generator.cache_key(body.old_code or "", body.new_code, pack_text, model)

    # Same code, same context, same model: reuse the tests (only within this repo).
    earlier = db.scalar(
        select(RunFunction)
        .join(Run, Run.id == RunFunction.run_id)
        .where(
            Run.repo_id == repo.id,
            RunFunction.cache_key == key,
            RunFunction.test_code.is_not(None),
        )
        .order_by(RunFunction.created_at.desc())
        .limit(1)
    )
    if earlier is not None:
        row.test_code, row.cache_key = earlier.test_code, key
        row.status, row.reason = "generated", None
        db.commit()
        return GenerateOut(
            function_id=row.id, status="generated", test_code=row.test_code, cached=True,
            import_line=line,
        )

    try:
        check_allowance(db, repo, settings)
    except AllowanceUsed:
        return _fail(db, row, "No result: the daily AI allowance has been used up.", line)

    messages = generator.build_messages(
        body.file, body.name, body.old_code, body.new_code, pack_text
    )
    try:
        result = complete(
            PURPOSE, messages, budget_key="default", max_tokens=generator.MAX_OUTPUT_TOKENS
        )
    except LLMError as error:
        return _fail(db, row, _LLM_REASONS.get(error.kind, _LLM_REASONS["unavailable"]), line)
    record(db, repo, PURPOSE, result)

    try:
        code = generator.extract_code(result.text)
    except ValueError:
        return _fail(db, row, "The AI did not return usable test code.", line)

    row.test_code, row.cache_key = code, key
    row.status, row.reason = "generated", None
    db.commit()
    return GenerateOut(
        function_id=row.id, status="generated", test_code=code, cached=False, import_line=line
    )
