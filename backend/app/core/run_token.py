"""The run token: our own short-lived pass for one run.

GitHub's identity token expires after a few minutes, but a run can take longer. So when a run
starts we hand the Action a token signed by us. It is valid for one run only, for about an hour.
"""

import time
import uuid

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.auth import bearer_scheme
from app.core.config import Settings, get_settings
from app.db.models.accounts import Repo
from app.db.models.runs import Run
from app.db.session import get_db

ISSUER = "prsentinel"
AUDIENCE = "prsentinel-run"


def _secret(settings: Settings) -> str:
    if settings.run_token_secret is None or not settings.run_token_secret.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runs are not configured on the server (RUN_TOKEN_SECRET missing).",
        )
    return settings.run_token_secret.get_secret_value()


def create_run_token(run_id: uuid.UUID, repo_id: uuid.UUID, settings: Settings) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(run_id),
            "repo": str(repo_id),
            "iat": now,
            "exp": now + settings.run_token_ttl_seconds,
        },
        _secret(settings),
        algorithm="HS256",
    )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_run(
    run_id: uuid.UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> Run:
    """The run named in the URL, but only if the caller holds that run's token.

    Use this on every Action-facing endpoint after the run has started (/generate, /results,
    /agent/step, /finish). Features D and E: `run.repo_id` is the repo, load it with db.get(Repo, ...).
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Missing run token.")
    try:
        payload = jwt.decode(
            credentials.credentials,
            _secret(settings),
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "sub", "repo", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Run token expired.") from None
    except jwt.PyJWTError:
        raise _unauthorized("Invalid run token.") from None

    if payload["sub"] != str(run_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token is for another run.")

    run = db.get(Run, run_id)
    if run is None or str(run.repo_id) != payload["repo"] or db.get(Repo, run.repo_id) is None:
        raise _unauthorized("Run no longer exists.")
    return run
