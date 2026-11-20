"""What the scanners found in a run, as one shared view for the PR comment and the dashboard.

Scanner output is untrusted text (it quotes the user's code and paths), so callers must put it in
code spans or render it as plain text. "No new findings" never means "safe": scanners only find
some kinds of problems, and a scanner that could not run is listed as such, never as a pass.
"""

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.findings import Finding
from app.db.models.runs import Run

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
ORIGIN_RANK = {"new": 0, "unknown": 1, "existing": 2}
SCANNER_NOTE = (
    "Scanners find only some kinds of problems. No finding is not proof the code is safe, "
    "and a finding is a hint, not proof."
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(text: object, limit: int) -> str:
    """One line of plain text: no control characters (Postgres refuses NUL), no line breaks."""
    return " ".join(_CONTROL.sub("", str(text)).split())[:limit]


def clean_path(path: str | None) -> str | None:
    if path is None:
        return None
    path = clean(path, 500).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path or None


def _sort_key(f: Finding) -> tuple:
    return (
        ORIGIN_RANK.get(f.origin, 1),
        SEVERITY_RANK.get(f.severity, 3),
        f.file or "",
        f.line or 0,
        f.rule_id,
    )


def load_findings(db: Session, run_id) -> list[Finding]:
    rows = list(db.scalars(select(Finding).where(Finding.run_id == run_id)))
    return sorted(rows, key=_sort_key)


def scanner_rows(run: Run) -> list[dict]:
    """What the runner said about each scanner (ran or not, and why), kept in `runs.summary`."""
    return list((run.summary or {}).get("scanners") or [])


def count_origins(rows: list[Finding]) -> dict[str, int]:
    counts = Counter(f.origin for f in rows)
    return {"new": counts["new"], "existing": counts["existing"], "unknown": counts["unknown"]}


def build_findings_view(run: Run, db: Session) -> dict:
    rows = load_findings(db, run.id)
    summary = run.summary or {}
    return {
        "run_id": str(run.id),
        "status": run.status,
        "scanners": scanner_rows(run),
        "counts": count_origins(rows),
        "omitted": int(summary.get("findings_omitted") or 0),
        "note": SCANNER_NOTE,
        "findings": [
            {
                "id": str(f.id),
                "tool": f.tool,
                "rule_id": f.rule_id,
                "severity": f.severity,
                "confidence": f.confidence,
                "file": f.file,
                "line": f.line,
                "message": f.message,
                "cwe": f.cwe,
                "origin": f.origin,
                "triage": f.triage,
                "triage_reason": f.triage_reason,
                "proof": f.proof_status,
                "proof_reason": f.proof_reason,
            }
            for f in rows
        ],
    }
