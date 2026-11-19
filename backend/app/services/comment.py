"""Builds the PR comment text. Everything that came from the repo is untrusted, so names and
reasons are put inside code spans after stripping anything that could break out of them.

With a database session the comment also shows what happened to the AI-written tests of each
changed function (feature D4). A function with no result is listed as "not tested", never as
passed."""

import html

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.run_functions import FunctionTestResult, RunFunction
from app.db.models.runs import Run
from app.services import classifier as c
from app.services.findings import SCANNER_NOTE, count_origins, load_findings, scanner_rows

COMMENT_MARKER = "<!-- prsentinel-comment -->"
MAX_LISTED = 10  # functions listed when there are no test results to show
MAX_FUNCTIONS_SHOWN = 15  # functions shown with test results
MAX_TESTS_SHOWN = 8  # tests listed inside one function's details
MAX_COMMENT_CHARS = 60_000  # GitHub refuses comments above 65,536
MAX_FINDINGS_SHOWN = 10  # scanner findings listed in the comment (all are on the dashboard)
SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡", "unknown": "⚪"}
AI_NOTE = "Tests are AI-generated. A catching test is a hint, not proof."


def dashboard_link(base_url: str | None, run_id: object) -> str | None:
    """Address of one run's page in the dashboard, or None when no usable address is set.
    Only plain http(s) addresses are used, so a mistyped or hostile setting cannot put a
    script link or markup into a comment."""
    base = (base_url or "").strip().rstrip("/")
    if not base.lower().startswith(("http://", "https://")):
        return None
    if any(ch.isspace() or ch in "<>()[]`\"'#" for ch in base):
        return None
    return f"{base}/#/runs/{run_id}"


def _code(text: object, limit: int = 120) -> str:
    cleaned = " ".join(str(text).replace("`", "'").split())[:limit]
    return f"`{cleaned}`"


def _html_code(text: object, limit: int = 120) -> str:
    """Like _code, but as HTML: GitHub does not render Markdown inside <summary>."""
    cleaned = " ".join(str(text).split())[:limit]
    return f"<code>{html.escape(cleaned)}</code>"


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
    if test.label == c.ODD:
        return "odd"
    if test.label == c.TEST_WRONG_ON_BEFORE:
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
    elif kind == "odd":
        text = (
            "fails on the old code but passes on the new code, so it matches the new behaviour: "
            "check that this change is intended"
        )
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


def _repair_note(tests: list[FunctionTestResult]) -> list[str]:
    rounds = max((t.attempt for t in tests), default=0)
    if not rounds:
        return []
    times = "once" if rounds == 1 else f"{rounds} times"
    return [
        f"The AI repaired these tests {times} after some of them failed on the old code too. "
        "Only the newest version is shown.",
        "",
    ]


def _mutation_lines(row: RunFunction) -> list[str]:
    """One line about the mutation check, with the honest caveat. Empty when never reported."""
    m = row.mutation
    if not m:
        return []
    if m.get("status") != "measured" or m.get("score") is None:
        reason = m.get("reason") or "no reason was given"
        return [f"Mutation check: not measured ({_code(reason, 150)}).", ""]
    killed, survived = int(m.get("killed", 0)), int(m.get("survived", 0))
    percent = round(100 * killed / (killed + survived)) if killed + survived else 0
    line = (
        f"Mutation check: the tests caught {killed} of {killed + survived} small deliberate "
        f"faults ({percent}%)."
    )
    extra = []
    if m.get("equivalent"):
        extra.append(f"{m['equivalent']} changed nothing and were left out")
    if m.get("timed_out") or m.get("could_not_run"):
        extra.append(f"{int(m.get('timed_out', 0)) + int(m.get('could_not_run', 0))} could not be run")
    if m.get("capped"):
        extra.append("only the first faults were tried")
    if m.get("stopped_early"):
        extra.append("stopped early because of the time limit")
    if extra:
        line += " (" + "; ".join(extra) + ")"
    lines = [line]
    survivors = [str(s) for s in (m.get("survivors") or [])][:5]
    if survivors:
        lines.append("Faults the tests missed: " + ", ".join(_code(s, 40) for s in survivors) + ".")
    lines.append("The score is a lower bound: some faults change nothing, so no test could catch them.")
    return lines + [""]


def _function_summary(tests: list[FunctionTestResult]) -> str:
    counts: dict[str, int] = {}
    for test in tests:
        kind = _kind(test)
        counts[kind] = counts.get(kind, 0) + 1
    parts = []
    if counts.get("catches"):
        n = counts["catches"]
        verb = "catches" if n == 1 else "catch"
        parts.append(f"⚠️ <b>{_plural(n, 'test')} {verb} a change</b>")
    if counts.get("flaky"):
        parts.append(f"🎲 {_plural(counts['flaky'], 'unstable test')}")
    if counts.get("odd"):
        parts.append(f"🔄 {_plural(counts['odd'], 'test')} match the new behaviour")
    if counts.get("unreliable"):
        parts.append(f"❓ {_plural(counts['unreliable'], 'unreliable test')}")
    if counts.get("new_fails"):
        parts.append(f"❓ {_plural(counts['new_fails'], 'failing test')} on a new function")
    if counts.get("same"):
        parts.append(f"➖ {_plural(counts['same'], 'test')} found no difference")
    return " · ".join(parts)


def _function_block(row: RunFunction, tests: list[FunctionTestResult]) -> list[str]:
    title = f"{_code(row.file + '::' + row.name)} ({_code(row.change_type, 20)})"
    html_title = f"{_html_code(row.file + '::' + row.name)} ({_html_code(row.change_type, 20)})"
    if row.status == "tested" and tests:
        shown = tests[:MAX_TESTS_SHOWN]
        lines = [
            "<details>",
            f"<summary>{html_title}: {_function_summary(tests)}</summary>",
            "",
            *_repair_note(tests),
            *[_test_line(t) for t in shown],
        ]
        if len(tests) > len(shown):
            lines.append(f"- … and {len(tests) - len(shown)} more")
        lines += [""]
        lines += _mutation_lines(row)
        lines += ["</details>", ""]
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


def _security_section(run: Run, db: Session) -> list[str]:
    """Scanner results (feature E1). Only findings that are new (or could not be compared with the
    old code) are listed; findings that already existed are only counted. A scanner that could
    not run is said so, and "no findings" is never worded as "safe"."""
    scanners = scanner_rows(run)
    rows = load_findings(db, run.id)
    if not scanners and not rows:
        return []

    lines = ["#### Security checks", ""]
    parts = []
    for s in scanners:
        tool = _code(s.get("tool", "?"), 20)
        if s.get("status") == "ran":
            note = f" ({_code(s['reason'], 150)})" if s.get("reason") else ""
            parts.append(f"{tool} ran{note}")
        else:
            reason = _code(s.get("reason") or "no reason was given", 150)
            parts.append(f"⚠️ {tool} **could not run** ({reason})")
    if parts:
        lines += ["Scanners: " + " · ".join(parts), ""]

    counts = count_origins(rows)
    ran_any = any(s.get("status") == "ran" for s in scanners)
    if counts["new"]:
        text = f"**{counts['new']} new** finding(s) introduced by this commit"
        if counts["existing"]:
            text += f"; {counts['existing']} already existed before it"
        if counts["unknown"]:
            text += f"; {counts['unknown']} could not be compared with the old code"
        lines.append(text + ".")
    elif counts["unknown"]:
        lines.append(
            f"Found {counts['unknown']} finding(s), but the old code could not be scanned, "
            "so nobody can tell whether this commit introduced them."
        )
    elif ran_any:
        text = "No new findings in this commit."
        if counts["existing"]:
            text += f" {counts['existing']} finding(s) already existed before it."
        lines.append(text)
    else:
        lines.append("No scanner ran, so nothing was checked. This is **not** a pass.")
    lines.append("")

    listed = [f for f in rows if f.origin in ("new", "unknown")]
    for f in listed[:MAX_FINDINGS_SHOWN]:
        where = f.file or "?"
        if f.line:
            where += f":{f.line}"
        icon = SEVERITY_ICON.get(f.severity, "⚪")
        lines.append(
            f"- {icon} {_code(f.rule_id, 60)} ({_code(f.tool, 20)}, {f.severity}) in "
            f"{_code(where, 150)}: {_code(f.message, 200)}"
        )
    if len(listed) > MAX_FINDINGS_SHOWN:
        lines.append(f"- … and {len(listed) - MAX_FINDINGS_SHOWN} more (see the dashboard)")
    if listed:
        lines.append("")
    lines += [f"<sub>{SCANNER_NOTE}</sub>", ""]
    return lines


def build_comment(
    run: Run, repo_full_name: str, db: Session | None = None, dashboard_url: str | None = None
) -> str:
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
    link = dashboard_link(dashboard_url, run.id)
    if link:
        lines.append(f"[View full details in the dashboard]({link})")
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

    if db is not None and run.status == "completed":
        security = _security_section(run, db)
        if security:
            lines.append("")
            lines += security

    lines.append("")
    lines.append("<sub>PRSentinel results are automated hints, not proof.</sub>")
    text = "\n".join(lines)
    if len(text) > MAX_COMMENT_CHARS:
        text = text[:MAX_COMMENT_CHARS].rsplit("\n", 1)[0] + "\n\n_(comment shortened)_"
    return text
