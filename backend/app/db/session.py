"""Database connection. The engine is created on first use, not at import time,
so the app (and the tests) start fine without a database configured.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def normalize_database_url(url: str) -> str:
    """Make a plain Postgres URL use the psycopg 3 driver.

    Supabase and Render hand out URLs starting with postgres:// or postgresql://.
    SQLAlchemy needs postgresql+psycopg:// to use the driver we install.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_database_url() -> str:
    secret = get_settings().database_url
    if secret is None or not secret.get_secret_value().strip():
        raise RuntimeError(
            "DATABASE_URL is not set. Put it in backend/.env (see .env.example)."
        )
    return normalize_database_url(secret.get_secret_value().strip())


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one database session per request."""
    with get_session_factory()() as session:
        yield session
