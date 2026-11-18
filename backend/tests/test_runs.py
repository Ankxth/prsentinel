import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.db.models.accounts import Repo, User
from app.db.models.runs import Run
from tests.conftest import bearer

CHANGED = {
    "contract_version": 1,
    "changed_files": 2,
    "changed_functions": [
        {"file": "app/a.py", "name": "add", "change_type": "modified"},
        {"file": "app/b.py", "name": "Cart.total", "change_type": "added"},
    ],
}


def start(client, token, body=None):
    return client.post("/v1/runs", json=body or CHANGED, headers=bearer(token))


# ------------------------------------------------------------------ starting a run

def test_start_run_happy_path(client, make_github_token, registered_repo, db):
    response = start(client, make_github_token(sha="b" * 40))
    assert response.status_code == 201
    body = response.json()
    assert body["repo"] == "alice/demo"
    assert body["expires_in"] == 3600
    assert body["limits"]["max_functions"] == 50
    assert body["contract_version"] == 1
    run = db.get(Run, uuid.UUID(body["run_id"]))
    assert run.status == "running"
    assert run.sha == "b" * 40
    assert run.event == "push"
    assert run.summary["changed_files"] == 2
    assert len(run.summary["changed_functions"]) == 2


def test_same_github_run_is_not_duplicated(client, make_github_token, registered_repo, db):
    first = start(client, make_github_token()).json()
    second = start(client, make_github_token())
    assert second.status_code == 200
    assert second.json()["run_id"] == first["run_id"]
    assert db.query(Run).count() == 1


def test_new_attempt_or_run_makes_a_new_row(client, make_github_token, registered_repo, db):
    start(client, make_github_token(run_id=1))
    start(client, make_github_token(run_id=1, run_attempt=2))
    start(client, make_github_token(run_id=2))
    assert db.query(Run).count() == 3


def test_pull_request_and_manual_runs_are_accepted(client, make_github_token, registered_repo):
    pr = make_github_token(event_name="pull_request", ref="refs/pull/7/merge", run_id=1)
    manual = make_github_token(event_name="workflow_dispatch", run_id=2)
    assert start(client, pr).status_code == 201
    assert start(client, manual).status_code == 201


def test_other_events_are_refused(client, make_github_token, registered_repo, db):
    response = start(client, make_github_token(event_name="schedule"))
    assert response.status_code == 422
    assert db.query(Run).count() == 0


def test_wrong_contract_version_is_refused(client, make_github_token, registered_repo):
    response = start(client, make_github_token(), {**CHANGED, "contract_version": 99})
    assert response.status_code == 400
    assert "Update the Action" in response.json()["detail"]


def test_repo_must_be_added_in_the_dashboard_first(client, make_github_token, db):
    response = start(client, make_github_token(repository_id=999, repository="x/unknown"))
    assert response.status_code == 403
    assert "dashboard" in response.json()["detail"]
    assert db.query(Run).count() == 0


def test_private_repo_is_refused(client, make_github_token, registered_repo):
    response = start(client, make_github_token(visibility="private"))
    assert response.status_code == 403


def test_renamed_repo_is_found_by_id_and_name_is_updated(
    client, make_github_token, registered_repo, db
):
    response = start(client, make_github_token(repository="alice/demo-renamed"))
    assert response.status_code == 201
    db.expire_all()
    assert db.get(Repo, registered_repo.id).github_name == "demo-renamed"


def test_a_repo_name_cannot_claim_another_repos_id(client, make_github_token, registered_repo):
    # Same name, different GitHub id: not our registered repo.
    response = start(client, make_github_token(repository="alice/demo", repository_id=555))
    assert response.status_code == 403


def test_too_many_functions_is_refused(client, make_github_token, registered_repo):
    many = [{"file": "a.py", "name": f"f{i}", "change_type": "added"} for i in range(501)]
    response = start(client, make_github_token(), {**CHANGED, "changed_functions": many})
    assert response.status_code == 422


# ----------------------------------------------------- GitHub token must be genuine

def test_no_token_is_401(client, registered_repo):
    assert client.post("/v1/runs", json=CHANGED).status_code == 401


def test_garbage_token_is_401(client, registered_repo):
    assert start(client, "nope").status_code == 401


def test_wrong_audience_is_401(client, make_github_token, registered_repo):
    assert start(client, make_github_token(audience="someone-else")).status_code == 401


def test_wrong_issuer_is_401(client, make_github_token, registered_repo):
    assert start(client, make_github_token(issuer="https://evil.example")).status_code == 401


def test_expired_token_is_401(client, make_github_token, registered_repo):
    assert start(client, make_github_token(expires_in=-5)).status_code == 401


def test_token_signed_by_someone_else_is_401(client, make_github_token, registered_repo):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert start(client, make_github_token(key=other)).status_code == 401


def test_shared_secret_token_is_401(client, registered_repo):
    forged = jwt.encode(
        {"iss": "https://token.actions.githubusercontent.com", "aud": "prsentinel",
         "sub": "x", "exp": int(time.time()) + 300, "repository_id": "100"},
        "a-secret-that-is-long-enough-0123456789", algorithm="HS256",
    )
    assert start(client, forged).status_code == 401


def test_unsigned_token_is_401(client, registered_repo):
    forged = jwt.encode({"aud": "prsentinel", "sub": "x"}, key=None, algorithm="none")
    assert start(client, forged).status_code == 401


def test_a_dashboard_sign_in_token_cannot_start_a_run(client, make_token, registered_repo):
    assert start(client, make_token()).status_code == 401


def test_runs_need_a_server_secret(client, make_github_token, registered_repo, monkeypatch):
    from app.core.config import Settings, get_settings

    monkeypatch.delenv("RUN_TOKEN_SECRET", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    assert start(client, make_github_token()).status_code == 503


# ------------------------------------------------------------------- finishing a run

def started(client, make_github_token, **kwargs):
    data = start(client, make_github_token(**kwargs)).json()
    return data["run_id"], data["run_token"]


def finish(client, run_id, token, body):
    return client.post(f"/v1/runs/{run_id}/finish", json=body, headers=bearer(token))


def test_finish_completed_returns_the_comment(client, make_github_token, registered_repo, db):
    run_id, token = started(client, make_github_token)
    response = finish(client, run_id, token, {"status": "completed"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["comment_marker"] in body["comment_markdown"]
    assert "`app/a.py::add`" in body["comment_markdown"]
    assert "not proof" in body["comment_markdown"]
    run = db.get(Run, uuid.UUID(run_id))
    assert run.status == "completed" and run.finished_at is not None


def test_could_not_run_is_clearly_not_a_pass(client, make_github_token, registered_repo):
    run_id, token = started(client, make_github_token)
    body = finish(
        client, run_id, token, {"status": "could_not_run", "reason": "pip install failed"}
    ).json()
    assert "Could not run" in body["comment_markdown"]
    assert "not** a pass" in body["comment_markdown"]
    assert "pip install failed" in body["comment_markdown"]


def test_comment_cannot_be_broken_by_hostile_names(client, make_github_token, registered_repo):
    nasty = {
        "contract_version": 1,
        "changed_files": 1,
        "changed_functions": [
            {"file": "a.py", "name": "x`\n[click](http://evil)\n<img src=x>", "change_type": "added"}
        ],
    }
    data = start(client, make_github_token(), nasty).json()
    comment = finish(client, data["run_id"], data["run_token"], {"status": "completed"}).json()[
        "comment_markdown"
    ]
    assert "\n[click]" not in comment
    assert comment.count("`") % 2 == 0


def test_finishing_twice_is_409(client, make_github_token, registered_repo):
    run_id, token = started(client, make_github_token)
    assert finish(client, run_id, token, {"status": "completed"}).status_code == 200
    assert finish(client, run_id, token, {"status": "completed"}).status_code == 409


def test_bad_status_is_422(client, make_github_token, registered_repo):
    run_id, token = started(client, make_github_token)
    assert finish(client, run_id, token, {"status": "exploded"}).status_code == 422


def test_finish_needs_the_run_token(client, make_github_token, registered_repo, make_token):
    run_id, _ = started(client, make_github_token)
    assert client.post(f"/v1/runs/{run_id}/finish", json={"status": "completed"}).status_code == 401
    assert finish(client, run_id, "junk", {"status": "completed"}).status_code == 401
    # a GitHub identity token or a dashboard token is not a run token
    assert finish(client, run_id, make_github_token(), {"status": "completed"}).status_code == 401
    assert finish(client, run_id, make_token(), {"status": "completed"}).status_code == 401


def test_a_run_token_only_works_for_its_own_run(client, make_github_token, registered_repo):
    run_a, token_a = started(client, make_github_token, run_id=1)
    run_b, _ = started(client, make_github_token, run_id=2)
    assert finish(client, run_b, token_a, {"status": "completed"}).status_code == 403
    assert finish(client, run_a, token_a, {"status": "completed"}).status_code == 200


def test_expired_run_token_is_401(client, make_github_token, registered_repo, monkeypatch):
    monkeypatch.setenv("RUN_TOKEN_TTL_SECONDS", "-10")
    from app.core.config import get_settings

    get_settings.cache_clear()
    run_id, token = started(client, make_github_token)
    assert finish(client, run_id, token, {"status": "completed"}).status_code == 401


def test_run_token_signed_with_another_secret_is_401(client, make_github_token, registered_repo):
    run_id, _ = started(client, make_github_token)
    forged = jwt.encode(
        {"iss": "prsentinel", "aud": "prsentinel-run", "sub": run_id, "repo": str(uuid.uuid4()),
         "exp": int(time.time()) + 300},
        "some-other-secret-0123456789abcdefghijkl", algorithm="HS256",
    )
    assert finish(client, run_id, forged, {"status": "completed"}).status_code == 401


# --------------------------------------------------- dashboard views and isolation

def test_owner_sees_their_runs(client, make_github_token, registered_repo, db, make_token):
    run_id, _ = started(client, make_github_token)
    owner = db.query(User).one()
    headers = bearer(make_token(user_id=owner.id, login=owner.github_login, github_id=str(owner.github_id)))

    listed = client.get(f"/v1/repos/{registered_repo.id}/runs", headers=headers)
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == [run_id]
    assert client.get(f"/v1/runs/{run_id}", headers=headers).json()["status"] == "running"


def test_other_users_cannot_see_runs(client, make_github_token, registered_repo, make_token):
    run_id, _ = started(client, make_github_token)
    stranger = bearer(make_token(user_id=uuid.uuid4(), login="mallory"))
    assert client.get(f"/v1/repos/{registered_repo.id}/runs", headers=stranger).status_code == 404
    assert client.get(f"/v1/runs/{run_id}", headers=stranger).status_code == 404


def test_run_token_does_not_open_dashboard_endpoints(client, make_github_token, registered_repo):
    run_id, token = started(client, make_github_token)
    assert client.get(f"/v1/runs/{run_id}", headers=bearer(token)).status_code == 401
    assert client.get(f"/v1/repos/{registered_repo.id}/runs", headers=bearer(token)).status_code == 401


def test_dashboard_needs_sign_in(client, registered_repo):
    assert client.get(f"/v1/repos/{registered_repo.id}/runs").status_code == 401
    assert client.get(f"/v1/runs/{uuid.uuid4()}").status_code == 401


def test_deleting_the_account_deletes_runs(client, make_github_token, registered_repo, db, make_token):
    started(client, make_github_token)
    owner = db.query(User).one()
    headers = bearer(make_token(user_id=owner.id, login=owner.github_login, github_id=str(owner.github_id)))
    assert client.delete("/v1/me", headers=headers).status_code == 200
    db.expire_all()
    assert db.query(Run).count() == 0
