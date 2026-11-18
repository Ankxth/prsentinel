import uuid

from app.db.models.accounts import Repo, User
from app.main import create_app  # noqa: F401  (keeps import order stable)
from app.services.supabase_admin import get_auth_user_deleter
from tests.conftest import bearer


def sign_in(client, make_token, login):
    user_id = uuid.uuid4()
    headers = bearer(make_token(user_id=user_id, login=login))
    assert client.get("/v1/me", headers=headers).status_code == 200
    return user_id, headers


def add_repo(client, headers, github, owner, name, github_id):
    github.add(owner, name, github_id)
    response = client.post(
        "/v1/repos", json={"full_name": f"{owner}/{name}", "llm_opt_in": True}, headers=headers
    )
    assert response.status_code == 201


def use_deleter(client, result, calls):
    def fake(user_id):
        calls.append(user_id)
        return result

    client.app.dependency_overrides[get_auth_user_deleter] = lambda: fake


def test_requires_sign_in(client):
    assert client.delete("/v1/me").status_code == 401


def test_deletes_the_user_and_their_repos_only(client, make_token, github, db):
    alice_id, alice = sign_in(client, make_token, "alice")
    bob_id, bob = sign_in(client, make_token, "bob")
    add_repo(client, alice, github, "alice", "a1", 1)
    add_repo(client, bob, github, "bob", "b1", 2)
    calls = []
    use_deleter(client, True, calls)

    response = client.delete("/v1/me", headers=alice)

    assert response.status_code == 200
    assert response.json() == {"data_deleted": True, "login_removed": True}
    assert calls == [alice_id]
    db.expire_all()
    assert db.get(User, alice_id) is None
    assert db.query(Repo).filter(Repo.owner_user_id == alice_id).count() == 0
    assert db.get(User, bob_id) is not None
    assert db.query(Repo).filter(Repo.owner_user_id == bob_id).count() == 1


def test_data_is_deleted_even_if_login_removal_fails(client, make_token, db):
    user_id, headers = sign_in(client, make_token, "alice")
    use_deleter(client, False, [])

    response = client.delete("/v1/me", headers=headers)

    assert response.json() == {"data_deleted": True, "login_removed": False}
    db.expire_all()
    assert db.get(User, user_id) is None


def test_login_removal_is_skipped_without_a_secret_key(monkeypatch):
    from app.core.config import get_settings
    from app.services import supabase_admin

    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    called = []
    monkeypatch.setattr(supabase_admin.httpx, "delete", lambda *a, **k: called.append(1))
    assert supabase_admin.delete_auth_user(uuid.uuid4()) is False
    assert called == []
