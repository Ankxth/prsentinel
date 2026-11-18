"""Builds the PR comment text. Everything that came from the repo is untrusted, so names and
reasons are put inside code spans after stripping anything that could break out of them."""

from app.db.models.runs import Run

COMMENT_MARKER = "<!-- prsentinel-comment -->"
MAX_LISTED = 10


def _code(text: object, limit: int = 120) -> str:
    cleaned = " ".join(str(text).replace("`", "'").split())[:limit]
    return f"`{cleaned}`"


def build_comment(run: Run, repo_full_name: str) -> str:
    summary = run.summary or {}
    functions = summary.get("changed_functions") or []
    files = summary.get("changed_files", 0)

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
    elif not functions:
        lines.append("No changed Python functions were found in this commit. Nothing to check.")
    else:
        lines.append(f"Found **{len(functions)}** changed function(s) in **{files}** file(s):")
        lines.append("")
        for item in functions[:MAX_LISTED]:
            lines.append(
                f"- {_code(str(item.get('file', '?')) + '::' + str(item.get('name', '?')))}"
                f" ({_code(item.get('change_type', 'modified'), 20)})"
            )
        if len(functions) > MAX_LISTED:
            lines.append(f"- … and {len(functions) - MAX_LISTED} more")
        lines.append("")
        lines.append("Test generation and vulnerability checks are not switched on yet.")

    lines.append("")
    lines.append("<sub>PRSentinel results are automated hints, not proof.</sub>")
    return "\n".join(lines)
