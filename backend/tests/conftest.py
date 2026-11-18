"""Shared test setup: an in-memory database, fake Supabase tokens, a fake GitHub."""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import auth, oidc
from app.db.models.accounts import Repo, User
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.github import GitHubRepo, RepoLookupError, get_repo_lookup

SUPABASE_URL = "https://test-project.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("MAX_REPOS_PER_USER", "2")
    monkeypatch.setenv("RUN_TOKEN_SECRET", "test-run-secret-0123456789-abcdefghijklmnop")
    monkeypatch.setenv("GITHUB_OIDC_AUDIENCE", "prsentinel")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def signing_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def make_token(signing_key):
    def _make(
        user_id=None,
        login="alice",
        github_id=None,
        key=None,
        algorithm="ES256",
        issuer=ISSUER,
        audience="authenticated",
        expires_in=3600,
        metadata=None,
    ):
        payload = {
            "sub": str(user_id or uuid.uuid4()),
            "iss": issuer,
            "aud": audience,
            "exp": int(time.time()) + expires_in,
            "user_metadata": metadata
            if metadata is not None
            else {"user_name": login, "provider_id": github_id or str(abs(hash(login)) % 10**9), "avatar_url": f"https://a/{login}"},
        }
        return jwt.encode(payload, key or signing_key, algorithm=algorithm, headers={"kid": "k1"})

    return _make


@pytest.fixture(autouse=True)
def _public_key(monkeypatch, signing_key):
    monkeypatch.setattr(auth, "get_signing_key", lambda token, url: signing_key.public_key())


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db(db_engine):
    with Session(db_engine) as session:
        yield session


class FakeGitHub:
    """Stands in for GitHub. Add repos with .add(); anything else is 'not found'."""

    def __init__(self):
        self.repos: dict[str, GitHubRepo] = {}
        self.error: RepoLookupError | None = None
        self.calls = 0

    def add(self, owner, name, github_id, private=False, branch="main"):
        self.repos[f"{owner}/{name}".lower()] = GitHubRepo(
            github_id=github_id, owner=owner, name=name, default_branch=branch, is_private=private
        )

    def __call__(self, owner, name):
        self.calls += 1
        if self.error:
            raise self.error
        found = self.repos.get(f"{owner}/{name}".lower())
        if found is None:
            raise RepoLookupError("not_found", "Repository not found, or it is not public.")
        return found


@pytest.fixture
def github():
    return FakeGitHub()


@pytest.fixture
def client(db_engine, github):
    app = create_app()
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)

    def _get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_repo_lookup] = lambda: github
    return TestClient(app)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---- GitHub Actions identity tokens (feature B) -------------------------------------

GITHUB_ISSUER = "https://token.actions.githubusercontent.com"


@pytest.fixture(scope="session")
def github_signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _github_public_key(monkeypatch, github_signing_key):
    monkeypatch.setattr(oidc, "get_github_signing_key", lambda token: github_signing_key.public_key())


@pytest.fixture
def make_github_token(github_signing_key):
    """Builds a token like the one GitHub gives a workflow job."""

    def _make(
        repository="alice/demo",
        repository_id=100,
        sha="a" * 40,
        ref="refs/heads/main",
        event_name="push",
        run_id=5000,
        run_attempt=1,
        visibility="public",
        audience="prsentinel",
        issuer=GITHUB_ISSUER,
        expires_in=300,
        key=None,
        algorithm="RS256",
    ):
        payload = {
            "iss": issuer,
            "aud": audience,
            "sub": f"repo:{repository}:ref:{ref}",
            "exp": int(time.time()) + expires_in,
            "repository": repository,
            "repository_id": str(repository_id),
            "sha": sha,
            "ref": ref,
            "event_name": event_name,
            "run_id": str(run_id),
            "run_attempt": str(run_attempt),
            "repository_visibility": visibility,
        }
        return jwt.encode(payload, key or github_signing_key, algorithm=algorithm)

    return _make


@pytest.fixture
def registered_repo(db):
    """A user 'alice' who has added alice/demo (GitHub id 100) in the dashboard."""
    user = User(id=uuid.uuid4(), github_login="alice", github_id=1)
    db.add(user)
    db.flush()
    repo = Repo(
        owner_user_id=user.id,
        github_id=100,
        github_owner="alice",
        github_name="demo",
        default_branch="main",
        llm_opt_in=True,
        settings={},
    )
    db.add(repo)
    db.commit()
    return repo
