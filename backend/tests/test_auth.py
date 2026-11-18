import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from app.db.models.accounts import User
from tests.conftest import bearer


def test_me_requires_a_token(client):
    response = client.get("/v1/me")
    assert response.status_code == 401


def test_me_creates_the_user_on_first_sign_in(client, make_token, db):
    user_id = uuid.uuid4()
    response = client.get("/v1/me", headers=bearer(make_token(user_id=user_id, login="alice")))
    assert response.status_code == 200
    body = response.json()
    assert body["github_login"] == "alice"
    assert body["id"] == str(user_id)
    assert body["is_admin"] is False
    assert db.get(User, user_id) is not None


def test_me_is_idempotent(client, make_token, db):
    token = make_token(user_id=uuid.uuid4(), login="alice")
    client.get("/v1/me", headers=bearer(token))
    client.get("/v1/me", headers=bearer(token))
    assert db.query(User).count() == 1


def test_renamed_github_login_is_updated(client, make_token):
    user_id = uuid.uuid4()
    client.get("/v1/me", headers=bearer(make_token(user_id=user_id, login="alice")))
    response = client.get("/v1/me", headers=bearer(make_token(user_id=user_id, login="alice2")))
    assert response.json()["github_login"] == "alice2"


def test_expired_token_is_rejected(client, make_token):
    response = client.get("/v1/me", headers=bearer(make_token(expires_in=-10)))
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_wrong_audience_is_rejected(client, make_token):
    assert client.get("/v1/me", headers=bearer(make_token(audience="other"))).status_code == 401


def test_wrong_issuer_is_rejected(client, make_token):
    token = make_token(issuer="https://evil.example/auth/v1")
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_token_signed_with_another_key_is_rejected(client, make_token):
    other = ec.generate_private_key(ec.SECP256R1())
    assert client.get("/v1/me", headers=bearer(make_token(key=other))).status_code == 401


def test_unsigned_token_is_rejected(client):
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": "x", "exp": 9999999999},
        key=None,
        algorithm="none",
    )
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_shared_secret_token_is_rejected_when_no_secret_is_configured(client):
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "exp": 9999999999},
        key="guess",
        algorithm="HS256",
    )
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_shared_secret_token_works_when_secret_is_configured(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_JWT_SECRET", "legacy-secret-value-0123456789abcdef")
    get_settings.cache_clear()
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "iss": "https://test-project.supabase.co/auth/v1",
            "exp": 9999999999,
            "user_metadata": {"user_name": "legacy"},
        },
        key="legacy-secret-value-0123456789abcdef",
        algorithm="HS256",
    )
    assert client.get("/v1/me", headers=bearer(token)).status_code == 200


def test_garbage_token_is_rejected(client):
    assert client.get("/v1/me", headers=bearer("not-a-token")).status_code == 401


def test_sign_in_without_github_username_is_rejected(client, make_token):
    response = client.get("/v1/me", headers=bearer(make_token(metadata={})))
    assert response.status_code == 401


def test_login_not_configured_gives_503(client, make_token, monkeypatch):
    from app.core.config import Settings, get_settings

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    assert client.get("/v1/me", headers=bearer(make_token())).status_code == 503


def test_same_github_account_with_a_new_sign_in_id_is_409_not_a_crash(client, make_token):
    first = make_token(user_id=uuid.uuid4(), login="alice", github_id="777")
    assert client.get("/v1/me", headers=bearer(first)).status_code == 200
    second = make_token(user_id=uuid.uuid4(), login="alice-renamed", github_id="777")
    assert client.get("/v1/me", headers=bearer(second)).status_code == 409
