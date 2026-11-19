"""Rows for the functions a run has to check (feature D)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.run_functions import RunFunction
from app.db.models.runs import Run


def register_functions(db: Session, run: Run, functions: list[dict]) -> list[RunFunction]:
    """Create one row per changed function. Safe to call twice: existing rows are kept.

    Deleted functions have nothing left to test, so they start as `skipped`.
    """
    existing = {
        (row.file, row.name): row
        for row in db.scalars(select(RunFunction).where(RunFunction.run_id == run.id))
    }
    rows = []
    for item in functions:
        key = (item["file"], item["name"])
        row = existing.get(key)
        if row is None:
            deleted = item.get("change_type") == "deleted"
            row = RunFunction(
                run_id=run.id,
                file=item["file"],
                name=item["name"],
                change_type=item.get("change_type", "modified"),
                status="skipped" if deleted else "pending",
                reason="The function was deleted, so there is nothing to test." if deleted else None,
            )
            db.add(row)
            existing[key] = row
        rows.append(row)
    db.commit()
    return rows
