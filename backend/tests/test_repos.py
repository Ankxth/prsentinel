import uuid

from tests.conftest import bearer
from app.services.github import RepoLookupError, parse_full_name

import pytest


def sign_in(client, make_token, login):
    token = make_token(user_id=uuid.uuid4(), login=login)
    headers = bearer(token)
    assert client.get("/v1/me", headers=headers).status_code == 200
    return headers


def add(client, headers, full_name="alice/demo", agree=True):
    return client.post("/v1/repos", json={"full_name": full_name, "llm_opt_in": agree}, headers=headers)


def test_repos_require_sign_in(client):
    assert client.get("/v1/repos").status_code == 401
    assert client.post("/v1/repos", json={"full_name": "a/b", "llm_opt_in": True}).status_code == 401


def test_add_list_get_delete(client, make_token, github):
    github.add("alice", "demo", 100, branch="develop")
    h = sign_in(client, make_token, "alice")

    created = add(client, h)
    assert created.status_code == 201
    repo = created.json()
    assert repo["full_name"] == "alice/demo"
    assert repo["default_branch"] == "develop"
    assert repo["llm_opt_in"] is True

    assert [r["id"] for r in client.get("/v1/repos", headers=h).json()] == [repo["id"]]
    assert client.get(f"/v1/repos/{repo['id']}", headers=h).status_code == 200
    assert client.delete(f"/v1/repos/{repo['id']}", headers=h).status_code == 204
    assert client.get("/v1/repos", headers=h).json() == []


def test_must_agree_to_send_code_to_ai(client, make_token, github):
    github.add("alice", "demo", 100)
    h = sign_in(client, make_token, "alice")
    response = add(client, h, agree=False)
    assert response.status_code == 422
    assert github.calls == 0  # rejected before any GitHub call


def test_private_repo_is_rejected(client, make_token, github):
    github.add("alice", "secret", 200, private=True)
    h = sign_in(client, make_token, "alice")
    assert add(client, h, "alice/secret").status_code == 422


def test_unknown_repo_is_404(client, make_token):
    h = sign_in(client, make_token, "alice")
    assert add(client, h, "alice/nope").status_code == 404


def test_github_trouble_is_reported(client, make_token, github):
    h = sign_in(client, make_token, "alice")
    github.error = RepoLookupError("rate_limited", "GitHub is limiting lookups. Try again later.")
    assert add(client, h).status_code == 503
    github.error = RepoLookupError("unavailable", "Could not reach GitHub. Try again.")
    assert add(client, h).status_code == 502


@pytest.mark.parametrize("bad", ["", "no-slash", "a/b/c", "a b/c", "../etc/passwd", "a/b?x=1", "https://evil.com/a/b"])
def test_bad_names_are_rejected_before_any_lookup(client, make_token, github, bad):
    h = sign_in(client, make_token, "alice")
    assert add(client, h, bad).status_code == 422
    assert github.calls == 0


def test_pasted_github_link_is_accepted(client, make_token, github):
    github.add("alice", "demo", 100)
    h = sign_in(client, make_token, "alice")
    assert add(client, h, "https://github.com/alice/demo/").status_code == 201


def test_parse_full_name():
    assert parse_full_name("psf/requests") == ("psf", "requests")
    assert parse_full_name("https://github.com/psf/requests.git") == ("psf", "requests")


def test_same_repo_twice_is_409(client, make_token, github):
    github.add("alice", "demo", 100)
    h = sign_in(client, make_token, "alice")
    assert add(client, h).status_code == 201
    assert add(client, h).status_code == 409


def test_repo_cap(client, make_token, github):
    for i in range(3):
        github.add("alice", f"r{i}", 10 + i)
    h = sign_in(client, make_token, "alice")
    assert add(client, h, "alice/r0").status_code == 201
    assert add(client, h, "alice/r1").status_code == 201
    third = add(client, h, "alice/r2")
    assert third.status_code == 403
    assert "limit" in third.json()["detail"].lower()


# ---- one user must never see or change another user's repos -------------------------

def test_users_cannot_see_each_others_repos(client, make_token, github):
    github.add("alice", "demo", 100)
    alice = sign_in(client, make_token, "alice")
    bob = sign_in(client, make_token, "bob")
    repo_id = add(client, alice).json()["id"]

    assert client.get("/v1/repos", headers=bob).json() == []
    assert client.get(f"/v1/repos/{repo_id}", headers=bob).status_code == 404


def test_user_cannot_delete_someone_elses_repo(client, make_token, github):
    github.add("alice", "demo", 100)
    alice = sign_in(client, make_token, "alice")
    bob = sign_in(client, make_token, "bob")
    repo_id = add(client, alice).json()["id"]

    assert client.delete(f"/v1/repos/{repo_id}", headers=bob).status_code == 404
    assert client.get(f"/v1/repos/{repo_id}", headers=alice).status_code == 200


def test_another_account_cannot_claim_the_same_github_repo(client, make_token, github):
    github.add("alice", "demo", 100)
    alice = sign_in(client, make_token, "alice")
    bob = sign_in(client, make_token, "bob")
    assert add(client, alice).status_code == 201
    response = add(client, bob)
    assert response.status_code == 409
    assert client.get("/v1/repos", headers=bob).json() == []


def test_unknown_repo_id_is_404(client, make_token):
    h = sign_in(client, make_token, "alice")
    assert client.get(f"/v1/repos/{uuid.uuid4()}", headers=h).status_code == 404
    assert client.delete(f"/v1/repos/{uuid.uuid4()}", headers=h).status_code == 404
