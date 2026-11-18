import hashlib
import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings
from app.db.models.accounts import Repo, User
from app.db.models.context import ContextEdge, ContextFile, ContextState
from app.db.models.runs import Run
from tests.conftest import bearer
from tests.test_migrations import alembic_config

START = {"contract_version": 1, "changed_files": 0, "changed_functions": []}


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def facts(path: str, content: str = "x", **extra) -> dict:
    base = {
        "path": path,
        "hash": h(path + content),
        "is_test": path.startswith("tests/"),
        "parse_error": None,
        "symbols": [
            {"name": "add", "kind": "function", "signature": "(item, cart=None)",
             "doc": "Add an item.", "lines": [1, 4], "decorators": [], "bases": [],
             "is_fixture": False}
        ],
        "imports": [{"module": "os", "name": None, "asname": None, "level": 0}],
        "calls": [{"in": "add", "name": "len", "line": 2}],
        "dynamic_calls": 1,
    }
    base.update(extra)
    return base


def manifest_of(items: list[dict]) -> dict:
    return {i["path"]: i["hash"] for i in items}


class Run_:
    """A started run and helpers to call the context endpoints with its token."""

    def __init__(self, client, token_maker, run_id=5000, repo="alice/demo", repo_id=100, sha="a" * 40):
        gh = token_maker(run_id=run_id, repository=repo, repository_id=repo_id, sha=sha)
        body = client.post("/v1/runs", json=START, headers=bearer(gh)).json()
        self.client = client
        self.id = body["run_id"]
        self.token = body["run_token"]
        self.sha = sha

    def _post(self, suffix, body):
        return self.client.post(
            f"/v1/runs/{self.id}/context/{suffix}", json=body, headers=bearer(self.token)
        )

    def manifest(self, items, total=None, skipped=0):
        return self._post(
            "manifest",
            {"files": manifest_of(items), "total_files": total if total is not None else len(items),
             "skipped_files": skipped},
        )

    def files(self, items):
        return self._post("files", {"files": items})

    def commit(self, **body):
        return self._post("commit", body)

    def finish(self):
        return self.client.post(
            f"/v1/runs/{self.id}/finish",
            json={"status": "completed"},
            headers=bearer(self.token),
        )


@pytest.fixture
def run(client, make_github_token, registered_repo):
    return Run_(client, make_github_token)


def build(run, items):
    assert run.manifest(items).status_code == 200
    assert run.files(items).status_code == 200
    response = run.commit()
    assert response.status_code == 200, response.text
    return response


# ------------------------------------------------------------------ the happy path

def test_first_build_asks_for_everything_then_completes(run, db, registered_repo):
    items = [facts("cart.py"), facts("tests/test_cart.py")]
    first = run.manifest(items)
    assert first.status_code == 200
    assert first.json() == {
        "need": ["cart.py", "tests/test_cart.py"], "unchanged": 0, "removed": 0,
        "max_batch_files": 100,
    }
    assert run.files(items).json() == {"saved": 2}
    done = run.commit().json()
    assert done == {"status": "complete", "built_from_sha": "a" * 40, "partial": False,
                    "python_files": 2}

    state = db.get(ContextState, registered_repo.id)
    assert state.status == "complete"
    assert state.pending_manifest == {}
    assert state.building_run_id is None
    assert state.repo_facts["test_files"] == 1
    row = db.query(ContextFile).filter_by(path="cart.py").one()
    assert row.symbols[0]["name"] == "add"
    assert row.calls == [{"in": "add", "name": "len", "line": 2}]
    assert row.built_from_sha == "a" * 40


def calling(path, symbol, name, calls_name, **extra):
    """Facts for a file with one function `symbol` that calls `calls_name`."""
    return facts(
        path,
        symbols=[{"name": symbol, "kind": "function", "signature": "()", "doc": "",
                  "lines": [1, 2], "decorators": [], "bases": [], "is_fixture": False}],
        imports=extra.pop("imports", []),
        calls=[{"in": symbol, "name": calls_name, "line": 2}],
        **extra,
    )


def test_commit_builds_the_call_graph_and_rebuilds_it_on_the_next_commit(
    client, make_github_token, registered_repo, db
):
    first = Run_(client, make_github_token, run_id=1)
    lib = facts("lib.py", symbols=[
        {"name": "helper", "kind": "function", "signature": "()", "doc": "", "lines": [1, 2],
         "decorators": [], "bases": [], "is_fixture": False}], calls=[], imports=[])
    app = calling("app.py", "main", "main", "helper",
                  imports=[{"module": "lib", "name": "helper", "asname": None, "level": 0}])
    build(first, [lib, app])
    first.finish()
    db.expire_all()
    edges = {(e.src_path, e.kind, e.target_name): e for e in db.query(ContextEdge)}
    call_edge = edges[("app.py", "call", "helper")]
    assert (call_edge.status, call_edge.resolved) == ("resolved", True)
    assert (call_edge.dst_path, call_edge.dst_symbol) == ("lib.py", "helper")
    assert call_edge.line == 2 and call_edge.built_from_sha == "a" * 40
    assert edges[("app.py", "import", "lib.helper")].dst_path == "lib.py"

    # lib.py loses `helper`: app.py did not change, yet its call must stop resolving
    second = Run_(client, make_github_token, run_id=2, sha="b" * 40)
    lib2 = facts("lib.py", content="v2", symbols=[], calls=[], imports=[])
    build(second, [lib2, app])
    db.expire_all()
    rows = db.query(ContextEdge).filter_by(src_path="app.py", kind="call").all()
    assert len(rows) == 1  # rebuilt, not duplicated
    assert (rows[0].status, rows[0].dst_path) == ("unresolved", None)  # no stale link survives
    assert rows[0].built_from_sha == "b" * 40


def test_dashboard_shows_edge_counts(client, make_github_token, make_token, registered_repo):
    first = Run_(client, make_github_token, run_id=1)
    build(first, [facts("a.py")])  # one call to len() -> external
    response = client.get(
        f"/v1/repos/{registered_repo.id}/context", headers=alice_headers(make_token, registered_repo)
    )
    assert response.json()["edges"] == {"resolved": 0, "external": 1, "unresolved": 0}


def test_later_run_only_needs_changed_files_and_drops_removed_ones(
    client, make_github_token, registered_repo, db
):
    first = Run_(client, make_github_token, run_id=1)
    a, b, c = facts("a.py"), facts("b.py"), facts("c.py")
    build(first, [a, b, c])
    first.finish()

    second = Run_(client, make_github_token, run_id=2, sha="b" * 40)
    b2 = facts("b.py", content="changed")
    d = facts("d.py")
    response = second.manifest([a, b2, d])  # c.py was deleted, b.py edited, d.py is new
    assert response.json()["need"] == ["b.py", "d.py"]
    assert response.json()["unchanged"] == 1
    assert response.json()["removed"] == 1
    assert {r.path for r in db.query(ContextFile)} == {"a.py", "b.py"}  # c.py gone already

    assert second.files([b2, d]).json() == {"saved": 2}
    second.commit()
    db.expire_all()
    rows = {r.path: r for r in db.query(ContextFile)}
    assert set(rows) == {"a.py", "b.py", "d.py"}
    assert rows["b.py"].content_hash == b2["hash"]
    assert rows["a.py"].built_from_sha == "a" * 40  # untouched file keeps its older commit
    assert rows["b.py"].built_from_sha == "b" * 40
    assert db.get(ContextState, registered_repo.id).built_from_sha == "b" * 40


def test_unchanged_repo_needs_nothing(client, make_github_token, registered_repo):
    first = Run_(client, make_github_token, run_id=1)
    items = [facts("a.py")]
    build(first, items)
    first.finish()
    second = Run_(client, make_github_token, run_id=2)
    assert second.manifest(items).json()["need"] == []
    assert second.commit().status_code == 200


def test_partial_scan_is_recorded(run, db, registered_repo):
    items = [facts("a.py")]
    run.manifest(items, total=700, skipped=699)
    run.files(items)
    assert run.commit().json()["partial"] is True
    state = db.get(ContextState, registered_repo.id)
    assert (state.partial, state.total_files, state.skipped_files) == (True, 700, 699)


def test_pytest_config_hint_decides_framework(run, db, registered_repo):
    items = [facts("a.py", imports=[])]
    run.manifest(items)
    run.files(items)
    run.commit(pytest_config=True)
    assert db.get(ContextState, registered_repo.id).repo_facts["test_framework"] == "pytest"


# ------------------------------------------------------------------ order and checks

def test_commit_refuses_when_files_are_missing(run, db, registered_repo):
    items = [facts("a.py"), facts("b.py")]
    run.manifest(items)
    run.files([items[0]])
    response = run.commit()
    assert response.status_code == 409
    assert "1 file(s) have not been uploaded" in response.json()["detail"]
    assert "b.py" in response.json()["detail"]
    assert db.get(ContextState, registered_repo.id).status == "building"


def test_upload_must_follow_the_manifest(run):
    assert run.files([facts("a.py")]).status_code == 409
    assert run.commit().status_code == 409


def test_unrequested_path_and_changed_hash_are_refused(run, db):
    items = [facts("a.py")]
    run.manifest(items)
    assert run.files([facts("other.py")]).status_code == 422
    assert run.files([facts("a.py", content="different")]).status_code == 422
    assert db.query(ContextFile).count() == 0


def test_a_bad_file_in_a_batch_saves_nothing(run, db):
    items = [facts("a.py"), facts("b.py")]
    run.manifest(items)
    assert run.files([items[0], facts("zzz.py")]).status_code == 422
    assert db.query(ContextFile).count() == 0


def test_finished_run_cannot_update_context(run):
    items = [facts("a.py")]
    run.manifest(items)
    run.finish()
    assert run.manifest(items).status_code == 409
    assert run.files(items).status_code == 409
    assert run.commit().status_code == 409


def test_second_run_is_blocked_while_first_is_building_then_takes_over(
    client, make_github_token, registered_repo
):
    first = Run_(client, make_github_token, run_id=1)
    first.manifest([facts("a.py")])
    second = Run_(client, make_github_token, run_id=2)
    blocked = second.manifest([facts("a.py")])
    assert blocked.status_code == 409
    assert "Another run" in blocked.json()["detail"]
    first.finish()  # the first run ends, so the second may take over
    assert second.manifest([facts("a.py")]).status_code == 200
    assert first.commit().status_code == 409  # the first run lost its build


def test_same_run_can_restart_its_build(run):
    items = [facts("a.py")]
    assert run.manifest(items).status_code == 200
    assert run.manifest(items).status_code == 200


# ------------------------------------------------------------------ input validation

def test_validation(run):
    ok = facts("a.py")
    assert run.manifest([ok], total=1).status_code == 200
    bad_hash = run._post("manifest", {"files": {"a.py": "nothex"}})
    assert bad_hash.status_code == 422
    bad_path = run._post("manifest", {"files": {"a\nb.py": h("x")}})
    assert bad_path.status_code == 422
    assert run._post("manifest", {"files": {"x" * 501: h("x")}}).status_code == 422
    assert run.files([{**ok, "symbols": [{"name": "f", "kind": "weird", "lines": [1, 2]}]}]).status_code == 422
    assert run.files([{**ok, "hash": "nothex"}]).status_code == 422
    assert run._post("files", {"files": []}).status_code == 422


def test_file_limit(client, make_github_token, registered_repo, monkeypatch):
    monkeypatch.setenv("MAX_CONTEXT_FILES", "2")
    get_settings.cache_clear()
    run = Run_(client, make_github_token)
    response = run.manifest([facts("a.py"), facts("b.py"), facts("c.py")])
    assert response.status_code == 422
    assert "limit is 2" in response.json()["detail"]


def test_batch_limit_and_duplicates(client, make_github_token, registered_repo, monkeypatch):
    monkeypatch.setenv("MAX_CONTEXT_BATCH_FILES", "2")
    get_settings.cache_clear()
    run = Run_(client, make_github_token)
    items = [facts("a.py"), facts("b.py"), facts("c.py")]
    run.manifest(items)
    assert run.files(items).status_code == 422
    assert run.files([items[0], items[0]]).status_code == 422
    assert run.files(items[:2]).status_code == 200


def test_oversized_upload_is_refused(client, make_github_token, registered_repo, monkeypatch):
    monkeypatch.setenv("MAX_CONTEXT_UPLOAD_BYTES", "300")
    get_settings.cache_clear()
    run = Run_(client, make_github_token)
    assert run.manifest([facts(f"f{i}.py") for i in range(10)]).status_code == 413


# ------------------------------------------------------------------ who may call

def test_action_endpoints_need_the_run_token(run, client):
    url = f"/v1/runs/{run.id}/context/manifest"
    assert client.post(url, json={"files": {}}).status_code == 401
    assert client.post(url, json={"files": {}}, headers=bearer("garbage")).status_code == 401


def test_another_runs_token_is_refused(client, make_github_token, registered_repo):
    mine = Run_(client, make_github_token, run_id=1)
    other = Run_(client, make_github_token, run_id=2)
    response = client.post(
        f"/v1/runs/{mine.id}/context/manifest",
        json={"files": {}},
        headers=bearer(other.token),
    )
    assert response.status_code == 403


def test_one_repo_cannot_touch_anothers_context(client, make_github_token, registered_repo, db):
    bob = User(id=uuid.uuid4(), github_login="bob", github_id=2)
    db.add(bob)
    db.flush()
    db.add(Repo(owner_user_id=bob.id, github_id=200, github_owner="bob", github_name="x",
                default_branch="main", llm_opt_in=True, settings={}))
    db.commit()
    alice_run = Run_(client, make_github_token, run_id=1)
    bob_run = Run_(client, make_github_token, run_id=2, repo="bob/x", repo_id=200)
    build(alice_run, [facts("secret.py")])
    build(bob_run, [facts("other.py")])
    bob_repo = db.query(Repo).filter_by(github_id=200).one()
    assert [r.path for r in db.query(ContextFile).filter_by(repo_id=bob_repo.id)] == ["other.py"]
    assert [r.path for r in db.query(ContextFile).filter_by(repo_id=registered_repo.id)] == ["secret.py"]


# ------------------------------------------------------------------ dashboard view

def alice_headers(make_token, repo):
    return bearer(make_token(user_id=repo.owner_user_id, login="alice", github_id="1"))


def test_dashboard_view_for_the_owner(client, make_github_token, make_token, registered_repo):
    run = Run_(client, make_github_token, sha="c" * 40)
    build(run, [facts("a.py"), facts("tests/test_a.py", parse_error="syntax error (line 1)")])
    response = client.get(
        f"/v1/repos/{registered_repo.id}/context", headers=alice_headers(make_token, registered_repo)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["built_from_sha"] == "c" * 40
    assert body["latest_run_sha"] == "c" * 40
    assert body["file_count"] == 2
    assert [f["path"] for f in body["files"]] == ["a.py", "tests/test_a.py"]
    assert body["files"][0]["symbols"] == 1
    assert body["files"][1]["parse_error"] == "syntax error (line 1)"
    assert body["repo_facts"]["python_files"] == 2


def test_dashboard_view_of_an_empty_repo(client, make_token, registered_repo):
    body = client.get(
        f"/v1/repos/{registered_repo.id}/context", headers=alice_headers(make_token, registered_repo)
    ).json()
    assert body["status"] == "empty"
    assert body["files"] == []
    assert body["built_from_sha"] is None


def test_other_users_cannot_see_the_context(client, make_token, make_github_token, registered_repo):
    run = Run_(client, make_github_token)
    build(run, [facts("a.py")])
    bob = bearer(make_token(login="bob"))
    assert client.get(f"/v1/repos/{registered_repo.id}/context", headers=bob).status_code == 404
    assert client.get(f"/v1/repos/{registered_repo.id}/context").status_code == 401


# ------------------------------------------------------------------ cleanup and migration

def test_removing_the_repo_removes_its_context(client, make_github_token, make_token, registered_repo, db):
    run = Run_(client, make_github_token)
    build(run, [facts("a.py")])
    response = client.delete(
        f"/v1/repos/{registered_repo.id}", headers=alice_headers(make_token, registered_repo)
    )
    assert response.status_code == 204
    db.expire_all()
    assert db.query(ContextFile).count() == 0
    assert db.query(ContextState).count() == 0
    assert db.query(Run).count() == 0


def test_migration_creates_context_tables(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")
    tables = inspect(create_engine(url)).get_table_names()
    assert {"context_state", "context_files", "context_edges"} <= set(tables)
    command.downgrade(alembic_config(), "0003")
    tables = inspect(create_engine(url)).get_table_names()
    assert not ({"context_state", "context_files", "context_edges"} & set(tables))
    get_settings.cache_clear()
