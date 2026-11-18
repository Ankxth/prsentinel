"""Dashboard login: check the Supabase token sent by the browser.

The browser signs in with GitHub through Supabase and receives a signed token (JWT).
It sends that token as `Authorization: Bearer <token>`. Here we:
  1. check the signature against Supabase's public keys (or the shared secret on
     old projects), the issuer, the audience and the expiry;
  2. find or create the user's row in `users`.

The backend never sees a GitHub password or a GitHub token.
"""

import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.accounts import User
from app.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False)

ASYMMETRIC_ALGORITHMS = {"ES256", "RS256"}


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    github_login: str | None
    github_id: int | None
    avatar_url: str | None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


@lru_cache
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600
    )


def get_signing_key(token: str, supabase_url: str):
    """Look up the public key that signed this token. Tests replace this function."""
    return _jwks_client(supabase_url).get_signing_key_from_jwt(token).key


def verify_token(token: str, settings: Settings) -> dict:
    if not settings.supabase_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login is not configured on the server (SUPABASE_URL missing).",
        )
    base_url = settings.supabase_url.rstrip("/")

    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        raise _unauthorized("Malformed token.") from None

    # The key is chosen by us from the algorithm, never taken from the token itself.
    if algorithm == "HS256":
        if settings.supabase_jwt_secret is None:
            raise _unauthorized("Unsupported token type.")
        key = settings.supabase_jwt_secret.get_secret_value()
    elif algorithm in ASYMMETRIC_ALGORITHMS:
        try:
            key = get_signing_key(token, base_url)
        except jwt.PyJWKClientConnectionError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not reach the login provider. Try again.",
            ) from None
        except jwt.PyJWTError:
            raise _unauthorized("Unknown signing key.") from None
    else:
        raise _unauthorized("Unsupported token type.")

    try:
        return jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=f"{base_url}/auth/v1",
            options={"require": ["exp", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired. Sign in again.") from None
    except jwt.PyJWTError:
        raise _unauthorized("Invalid token.") from None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> TokenClaims:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not signed in.")
    payload = verify_token(credentials.credentials, settings)
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError:
        raise _unauthorized("Invalid token.") from None
    meta = payload.get("user_metadata") or {}
    return TokenClaims(
        user_id=user_id,
        github_login=meta.get("user_name") or meta.get("preferred_username"),
        github_id=_to_int(meta.get("provider_id") or meta.get("sub")),
        avatar_url=meta.get("avatar_url"),
    )


def current_user(
    claims: TokenClaims = Depends(get_claims),
    db: Session = Depends(get_db),
) -> User:
    """The signed-in user's row. Created on first sign-in, refreshed on later ones."""
    if not claims.github_login:
        raise _unauthorized("This sign-in has no GitHub username. Sign in with GitHub.")

    user = db.get(User, claims.user_id)
    if user is None:
        taken = db.scalar(
            select(User).where(
                (User.github_login == claims.github_login)
                | ((User.github_id == claims.github_id) & (claims.github_id is not None))
            )
        )
        if taken is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This GitHub account is already linked to another sign-in.",
            )
        user = User(
            id=claims.user_id,
            github_login=claims.github_login,
            github_id=claims.github_id,
            avatar_url=claims.avatar_url,
        )
        db.add(user)
    else:
        user.avatar_url = claims.avatar_url or user.avatar_url
        if claims.github_login != user.github_login:
            clash = db.scalar(
                select(User).where(
                    User.github_login == claims.github_login, User.id != user.id
                )
            )
            if clash is None:
                user.github_login = claims.github_login
    db.commit()
    return user
