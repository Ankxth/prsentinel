"""What the dashboard run page shows: the same per-function results as the PR comment, as data.

It reuses the comment's own rules (`_latest_attempt`, `_kind`) so the page and the comment can
never disagree. It never includes the generated test code, only names, results, labels,
diagnoses and short failure texts. Everything that came from the repo is untrusted: the page
shows it as plain text.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.run_functions import FunctionTestResult, RunFunction
from app.db.models.runs import Run
from app.services.comment import AI_NOTE, _kind, _latest_attempt

MAX_MESSAGE_CHARS = 500
MAX_SURVIVORS = 5
KINDS = ("catches", "flaky", "odd", "unreliable", "new_fails", "same")


def _test(test: FunctionTestResult) -> dict:
    return {
        "name": test.test_name,
        "kind": _kind(test),
        "label": test.label,
        "diagnosis": test.diagnosis,
        "before": test.before_result,
        "after": test.after_result,
        "rerun_passes": test.rerun_passes,
        "rerun_fails": test.rerun_fails,
        "message": (test.message or "")[:MAX_MESSAGE_CHARS] or None,
    }


def _mutation(row: RunFunction) -> dict | None:
    """The mutation result in the same terms as the comment; None when never reported."""
    m = row.mutation
    if not m:
        return None
    if m.get("status") != "measured" or m.get("score") is None:
        return {
            "status": "not_measured",
            "reason": str(m.get("reason") or "no reason was given")[:200],
        }
    killed, survived = int(m.get("killed", 0)), int(m.get("survived", 0))
    scored = killed + survived
    return {
        "status": "measured",
        "killed": killed,
        "survived": survived,
        "percent": round(100 * killed / scored) if scored else 0,
        "equivalent": int(m.get("equivalent", 0)),
        "could_not_run": int(m.get("timed_out", 0)) + int(m.get("could_not_run", 0)),
        "capped": bool(m.get("capped")),
        "stopped_early": bool(m.get("stopped_early")),
        "survivors": [str(s)[:60] for s in (m.get("survivors") or [])][:MAX_SURVIVORS],
        "note": "The score is a lower bound: some faults change nothing, so no test could catch them.",
    }


def _function(row: RunFunction, all_tests: list[FunctionTestResult]) -> dict:
    tests = _latest_attempt(all_tests)
    counts = {kind: 0 for kind in KINDS}
    for test in tests:
        counts[_kind(test)] += 1
    # A function counts as tested only when it is marked tested AND has results, like the comment.
    shown = row.status == "tested" and bool(tests)
    return {
        "id": str(row.id),
        "file": row.file,
        "name": row.name,
        "change_type": row.change_type,
        "status": row.status if shown or row.status != "tested" else "could_not_test",
        "reason": row.reason,
        "counts": counts,
        "repair_rounds": max((t.attempt for t in all_tests), default=0),
        "tests": [_test(t) for t in tests] if shown else [],
        "mutation": _mutation(row) if shown else None,
    }


def build_run_view(run: Run, db: Session) -> dict:
    rows = list(
        db.scalars(
            select(RunFunction)
            .where(RunFunction.run_id == run.id)
            .order_by(RunFunction.file, RunFunction.name)
        )
    )
    tests = list(db.scalars(select(FunctionTestResult).where(FunctionTestResult.run_id == run.id)))
    by_function: dict = {}
    for test in tests:
        by_function.setdefault(test.run_function_id, []).append(test)
    functions = [_function(r, by_function.get(r.id, [])) for r in rows]
    return {
        "run_id": str(run.id),
        "status": run.status,
        "reason": (run.summary or {}).get("reason"),
        "ai_note": AI_NOTE,
        "functions": functions,
    }
