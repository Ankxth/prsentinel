"""Builds the PR comment text. Everything that came from the repo is untrusted, so names and
reasons are put inside code spans after stripping anything that could break out of them.

With a database session the comment also shows what happened to the AI-written tests of each
changed function (feature D4). A function with no result is listed as "not tested", never as
passed."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.run_functions import FunctionTestResult, RunFunction
from app.db.models.runs import Run
from app.services import classifier as c

COMMENT_MARKER = "<!-- prsentinel-comment -->"
MAX_LISTED = 10  # functions listed when there are no test results to show
MAX_FUNCTIONS_SHOWN = 15  # functions shown with test results
MAX_TESTS_SHOWN = 8  # tests listed inside one function's details
MAX_COMMENT_CHARS = 60_000  # GitHub refuses comments above 65,536
AI_NOTE = "Tests are AI-generated. A catching test is a hint, not proof."


def _code(text: object, limit: int = 120) -> str:
    cleaned = " ".join(str(text).replace("`", "'").split())[:limit]
    return f"`{cleaned}`"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _latest_attempt(tests: list[FunctionTestResult]) -> list[FunctionTestResult]:
    """Only the newest repair round of a function's tests is shown."""
    if not tests:
        return []
    newest = max(t.attempt for t in tests)
    return sorted((t for t in tests if t.attempt == newest), key=lambda t: t.test_name)


def _fails_now(test: FunctionTestResult) -> bool:
    return c.fails_on_new_code(test.after_result)


def _kind(test: FunctionTestResult) -> str:
    """One bucket per test, in the order that matters most to the reader."""
    if test.diagnosis == c.FLAKY:
        return "flaky"
    if test.label == c.CATCHES_CHANGE and test.diagnosis == c.REAL_BUG:
        return "catches"
    if test.label in (c.TEST_WRONG_ON_BEFORE, c.ODD):
        return "unreliable"
    if _fails_now(test):  # only reachable for an added function: no old code to compare
        return "new_fails"
    return "same"


def _test_line(test: FunctionTestResult) -> str:
    kind = _kind(test)
    name = _code(test.test_name, 100)
    if kind == "catches":
        text = "passes on the old code, fails on the new code"
    elif kind == "flaky":
        text = (
            f"unstable: {_plural(test.rerun_passes, 'rerun')} passed and "
            f"{test.rerun_fails} failed"
        )
    elif kind == "unreliable" and test.label == c.ODD:
        text = "fails on the old code but passes on the new code, so the test is odd"
    elif kind == "unreliable":
        text = "fails on the old code too, so the test itself may be wrong"
    elif kind == "new_fails":
        text = "fails on this new function, so check the test (there is no old code to compare)"
    else:
        text = "same result on the old and the new code"
    line = f"- {name}: {text}"
    if test.message and kind in ("catches", "flaky", "new_fails", "unreliable") and _fails_now(test):
        line += f"\n  - failure: {_code(test.message, 200)}"
    return line


def _function_summary(tests: list[FunctionTestResult]) -> str:
    counts: dict[str, int] = {}
    for test in tests:
        kind = _kind(test)
        counts[kind] = counts.get(kind, 0) + 1
    parts = []
    if counts.get("catches"):
        parts.append(f"⚠️ **{_plural(counts['catches'], 'test')} catch a change**")
    if counts.get("flaky"):
        parts.append(f"🎲 {_plural(counts['flaky'], 'unstable test')}")
    if counts.get("unreliable"):
        parts.append(f"❓ {_plural(counts['unreliable'], 'unreliable test')}")
    if counts.get("new_fails"):
        parts.append(f"❓ {_plural(counts['new_fails'], 'failing test')} on a new function")
    if counts.get("same"):
        parts.append(f"➖ {_plural(counts['same'], 'test')} found no difference")
    return " · ".join(parts)


def _function_block(row: RunFunction, tests: list[FunctionTestResult]) -> list[str]:
    title = f"{_code(row.file + '::' + row.name)} ({_code(row.change_type, 20)})"
    if row.status == "tested" and tests:
        shown = tests[:MAX_TESTS_SHOWN]
        lines = [
            "<details>",
            f"<summary>{title}: {_function_summary(tests)}</summary>",
            "",
            *[_test_line(t) for t in shown],
        ]
        if len(tests) > len(shown):
            lines.append(f"- … and {len(tests) - len(shown)} more")
        lines += ["", "</details>", ""]
        return lines
    if row.status in ("skipped", "could_not_generate", "could_not_test"):
        reason = row.reason or "no reason was given"
        return [f"- {title}: ⏭️ **not tested**: {_code(reason, 200)}", ""]
    return [f"- {title}: ⏭️ **no test result was reported**", ""]


def _tested_section(rows: list[RunFunction], tests: list[FunctionTestResult]) -> list[str]:
    by_function: dict = {}
    for test in tests:
        by_function.setdefault(test.run_function_id, []).append(test)
    latest = {row.id: _latest_attempt(by_function.get(row.id, [])) for row in rows}
    tested = [r for r in rows if r.status == "tested" and latest[r.id]]
    catching = [r for r in tested if any(_kind(t) == "catches" for t in latest[r.id])]

    lines = [
        f"**Tests:** {len(tested)} of {_plural(len(rows), 'function')} tested"
        + (f", {len(catching)} with a test that catches a change." if catching else "."),
        "",
    ]
    for row in rows[:MAX_FUNCTIONS_SHOWN]:
        lines += _function_block(row, latest[row.id])
    if len(rows) > MAX_FUNCTIONS_SHOWN:
        lines += [f"- … and {len(rows) - MAX_FUNCTIONS_SHOWN} more function(s)", ""]
    if tested:
        lines += [AI_NOTE, ""]
    return lines


def build_comment(run: Run, repo_full_name: str, db: Session | None = None) -> str:
    summary = run.summary or {}
    functions = summary.get("changed_functions") or []
    files = summary.get("changed_files", 0)

    rows: list[RunFunction] = []
    tests: list[FunctionTestResult] = []
    if db is not None:
        rows = list(
            db.scalars(
                select(RunFunction)
                .where(RunFunction.run_id == run.id)
                .order_by(RunFunction.file, RunFunction.name)
            )
        )
        tests = list(
            db.scalars(select(FunctionTestResult).where(FunctionTestResult.run_id == run.id))
        )
    has_results = any(r.status != "pending" for r in rows)

    lines = [COMMENT_MARKER, "### PRSentinel", ""]
    lines.append(f"Commit {_code(run.sha[:7])} in {_code(repo_full_name)}")
    lines.append("")

    if run.status == "could_not_run":
        reason = summary.get("reason") or "no reason was given"
        lines.append(f"⚠️ **Could not run:** {_code(reason, 200)}")
        lines.append("")
        lines.append("This is **not** a pass. Nothing was checked in this run.")
    elif run.status == "failed":
        reason = summary.get("reason") or "no reason was given"
        lines.append(f"❌ **The run failed:** {_code(reason, 200)}")
    elif not functions and not rows:
        lines.append("No changed Python functions were found in this commit. Nothing to check.")
    else:
        total = len(functions) or len(rows)
        lines.append(f"Found **{total}** changed function(s) in **{files}** file(s):")
        lines.append("")
        if has_results:
            lines += _tested_section(rows, tests)
        else:
            listed = functions or [
                {"file": r.file, "name": r.name, "change_type": r.change_type} for r in rows
            ]
            for item in listed[:MAX_LISTED]:
                lines.append(
                    f"- {_code(str(item.get('file', '?')) + '::' + str(item.get('name', '?')))}"
                    f" ({_code(item.get('change_type', 'modified'), 20)})"
                )
            if len(listed) > MAX_LISTED:
                lines.append(f"- … and {len(listed) - MAX_LISTED} more")
            lines.append("")
            lines.append(
                "No test results were reported for these functions, so nothing was tested. "
                "This is **not** a pass."
            )

    lines.append("")
    lines.append("<sub>PRSentinel results are automated hints, not proof.</sub>")
    text = "\n".join(lines)
    if len(text) > MAX_COMMENT_CHARS:
        text = text[:MAX_COMMENT_CHARS].rsplit("\n", 1)[0] + "\n\n_(comment shortened)_"
    return text
