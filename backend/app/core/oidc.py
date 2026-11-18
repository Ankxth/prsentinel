"""The Action's identity: GitHub's signed OIDC token.

Every GitHub Actions job can ask GitHub for a short-lived token that says which repository,
commit and run it belongs to. GitHub signs it, so nobody can forge it. We check:
  signature (GitHub's public keys), issuer, audience (ours), expiry, and that the repo is public.
The repository is found by its numeric id, which survives renames.
"""

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import bearer_scheme
from app.core.config import Settings, get_settings
from app.db.models.accounts import Repo
from app.db.session import get_db

GITHUB_ISSUER = "https://token.actions.githubusercontent.com"


@dataclass(frozen=True)
class GitHubClaims:
    repository: str
    repository_id: int
    sha: str
    ref: str
    event_name: str
    run_id: int
    run_attempt: int
    visibility: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{GITHUB_ISSUER}/.well-known/jwks", cache_keys=True, lifespan=3600)


def get_github_signing_key(token: str):
    """Look up the GitHub public key that signed this token. Tests replace this function."""
    return _jwks_client().get_signing_key_from_jwt(token).key


def verify_github_token(token: str, settings: Settings) -> dict:
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        raise _unauthorized("Malformed token.") from None
    if algorithm != "RS256":
        raise _unauthorized("Unsupported token type.")

    try:
        key = get_github_signing_key(token)
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reach GitHub to check the token. Try again.",
        ) from None
    except jwt.PyJWTError:
        raise _unauthorized("Unknown signing key.") from None

    try:
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.github_oidc_audience,
            issuer=GITHUB_ISSUER,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired.") from None
    except jwt.PyJWTError:
        raise _unauthorized("Invalid token.") from None


def _int(value, default: int | None = None) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise


def github_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> GitHubClaims:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Missing GitHub identity token.")
    payload = verify_github_token(credentials.credentials, settings)
    try:
        return GitHubClaims(
            repository=str(payload["repository"]),
            repository_id=_int(payload["repository_id"]),
            sha=str(payload["sha"]),
            ref=str(payload.get("ref", "")),
            event_name=str(payload.get("event_name", "")),
            run_id=_int(payload["run_id"]),
            run_attempt=_int(payload.get("run_attempt"), default=1),
            visibility=str(payload.get("repository_visibility", "")),
        )
    except (KeyError, TypeError, ValueError):
        raise _unauthorized("Token is missing required claims.") from None


def current_repo(
    claims: GitHubClaims = Depends(github_claims),
    db: Session = Depends(get_db),
) -> Repo:
    """The repository an Action call belongs to. It must be public and added in the dashboard."""
    if claims.visibility != "public":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only public repositories are supported.",
        )
    repo = db.scalar(select(Repo).where(Repo.github_id == claims.repository_id))
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{claims.repository} is not added yet. Add it in the PRSentinel dashboard first.",
        )
    owner, _, name = claims.repository.partition("/")
    if owner and name and (repo.github_owner, repo.github_name) != (owner, name):
        repo.github_owner, repo.github_name = owner, name  # the repo was renamed on GitHub
        db.commit()
    return repo
