"""Keeping a repo's code map up to date, one run at a time.

How a build works (the Action calls these in order, each with the run's token):

1. begin_build  The runner sends {path: hash} for the whole repo. We delete files that are gone,
                and answer with the paths whose hash is new or different ("need").
2. save_files   The runner sends facts for those paths (in batches). Each file must be one we
                asked for, and its hash must match the manifest.
3. commit_build We check nothing is missing, mark the map complete for this commit.

Only the run that began a build may continue it. Everything is scoped to one repo id.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.db.models.context import ContextEdge, ContextFile, ContextState
from app.db.models.runs import Run
from app.services.context_engine.graph import build_edges

STALE_BUILD_AFTER = timedelta(minutes=30)


class ContextError(Exception):
    """A problem the caller should see. `status` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def get_state(db: Session, repo_id: UUID) -> ContextState:
    state = db.get(ContextState, repo_id)
    if state is None:
        state = ContextState(
            repo_id=repo_id, status="empty", partial=False, total_files=0, skipped_files=0,
            pending_manifest={}, repo_facts={},
        )
        db.add(state)
        db.flush()
    return state


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _require_building(db: Session, run: Run) -> ContextState:
    state = get_state(db, run.repo_id)
    if run.status != "running":
        raise ContextError(409, "This run has already finished.")
    if state.status != "building" or state.building_run_id != run.id:
        raise ContextError(
            409, "No context update is open for this run. Send the manifest first."
        )
    return state


def begin_build(
    db: Session, run: Run, manifest: dict[str, str], total_files: int, skipped_files: int,
    max_files: int,
) -> dict:
    if run.status != "running":
        raise ContextError(409, "This run has already finished.")
    if len(manifest) > max_files:
        raise ContextError(422, f"Too many files ({len(manifest)}). The limit is {max_files}.")

    state = get_state(db, run.repo_id)
    if state.status == "building" and state.building_run_id not in (None, run.id):
        other = db.get(Run, state.building_run_id)
        busy = (
            other is not None
            and other.status == "running"
            and datetime.now(timezone.utc) - _aware(state.updated_at) < STALE_BUILD_AFTER
        )
        if busy:
            raise ContextError(409, "Another run is updating this repository's context.")

    existing = {
        path: content_hash
        for path, content_hash in db.execute(
            select(ContextFile.path, ContextFile.content_hash).where(
                ContextFile.repo_id == run.repo_id
            )
        )
    }
    removed = sorted(set(existing) - set(manifest))
    if removed:
        db.execute(
            delete(ContextFile).where(
                ContextFile.repo_id == run.repo_id, ContextFile.path.in_(removed)
            )
        )
    need = sorted(path for path, h in manifest.items() if existing.get(path) != h)

    state.status = "building"
    state.building_run_id = run.id
    state.target_sha = run.sha
    state.partial = skipped_files > 0
    state.total_files = total_files
    state.skipped_files = skipped_files
    state.pending_manifest = dict(manifest)
    db.commit()
    return {"need": need, "unchanged": len(manifest) - len(need), "removed": len(removed)}


def save_files(db: Session, run: Run, files: list[dict]) -> int:
    state = _require_building(db, run)
    manifest = state.pending_manifest or {}
    for item in files:
        if item["path"] not in manifest:
            raise ContextError(422, f"{item['path']} was not requested.")
        if item["hash"] != manifest[item["path"]]:
            raise ContextError(
                422, f"{item['path']} changed since the manifest was sent (hash mismatch)."
            )

    rows = {
        row.path: row
        for row in db.scalars(
            select(ContextFile).where(
                ContextFile.repo_id == run.repo_id,
                ContextFile.path.in_([f["path"] for f in files]),
            )
        )
    }
    for item in files:
        row = rows.get(item["path"])
        if row is None:
            row = ContextFile(repo_id=run.repo_id, path=item["path"])
            db.add(row)
        row.content_hash = item["hash"]
        row.is_test = item["is_test"]
        row.parse_error = item["parse_error"]
        row.symbols = item["symbols"]
        row.imports = item["imports"]
        row.calls = item["calls"]
        row.dynamic_calls = item["dynamic_calls"]
        row.built_from_sha = run.sha
    db.commit()
    return len(files)


def compute_repo_facts(db: Session, repo_id: UUID, hints: dict) -> dict:
    """Repository-wide facts, worked out from ALL stored files (not just this run's uploads)."""
    rows = list(db.scalars(select(ContextFile).where(ContextFile.repo_id == repo_id)))
    imported = {
        (imp.get("module") or "").split(".")[0] for row in rows for imp in (row.imports or [])
    }
    has_conftest = any(row.path.split("/")[-1] == "conftest.py" for row in rows)
    if "pytest" in imported or has_conftest or hints.get("pytest_config"):
        framework = "pytest"
    elif "unittest" in imported:
        framework = "unittest"
    else:
        framework = "unknown"
    return {
        "test_framework": framework,
        "has_conftest": has_conftest,
        "python_files": len(rows),
        "test_files": sum(1 for row in rows if row.is_test),
        "files_with_errors": sum(1 for row in rows if row.parse_error),
    }


def rebuild_edges(db: Session, repo_id: UUID, sha: str) -> dict:
    """Recompute every link of a repo from its stored facts. Returns counts per status.

    Links in one file can change when ANOTHER file changes (a function was added or moved), so the
    whole graph is rebuilt on every commit. It is name matching only, so this is fast.
    """
    rows = db.scalars(select(ContextFile).where(ContextFile.repo_id == repo_id)).all()
    edges = build_edges(
        {"path": r.path, "symbols": r.symbols, "imports": r.imports, "calls": r.calls} for r in rows
    )
    drop_edges(db, repo_id)
    if edges:
        db.execute(
            insert(ContextEdge),
            [{**edge, "repo_id": repo_id, "built_from_sha": sha} for edge in edges],
        )
    counts = {"resolved": 0, "external": 0, "unresolved": 0}
    for edge in edges:
        counts[edge["status"]] += 1
    return counts


def commit_build(db: Session, run: Run, hints: dict) -> ContextState:
    state = _require_building(db, run)
    manifest = state.pending_manifest or {}
    stored = {
        path: content_hash
        for path, content_hash in db.execute(
            select(ContextFile.path, ContextFile.content_hash).where(
                ContextFile.repo_id == run.repo_id
            )
        )
    }
    missing = sorted(p for p, h in manifest.items() if stored.get(p) != h)
    if missing:
        shown = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        raise ContextError(409, f"{len(missing)} file(s) have not been uploaded yet: {shown}")

    state.repo_facts = compute_repo_facts(db, run.repo_id, hints)
    rebuild_edges(db, run.repo_id, run.sha)
    state.status = "complete"
    state.built_from_sha = run.sha
    state.building_run_id = None
    state.pending_manifest = {}
    db.commit()
    return state


def drop_edges(db: Session, repo_id: UUID) -> None:
    """Remove every link of a repo. Used when the map is rebuilt (C3)."""
    db.execute(delete(ContextEdge).where(ContextEdge.repo_id == repo_id))
