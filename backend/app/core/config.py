"""All backend settings live here, read from environment variables or backend/.env.

Rules:
- No key or password is ever written in code.
- Every setting has a safe default, so the app starts with an empty .env.
- Secrets use SecretStr so they never show up when a settings object is printed.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ folder, worked out from this file so the .env is found wherever the
# server is started from.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    frontend_origin: str = "http://localhost:5173"

    database_url: SecretStr | None = None

    # Supabase login. SUPABASE_URL looks like https://<project-ref>.supabase.co
    supabase_url: str | None = None
    # Only needed if the project still signs tokens with the old shared secret (HS256).
    supabase_jwt_secret: SecretStr | None = None

    # Optional GitHub token (no special permissions needed). Without one, GitHub's
    # open API allows only 60 lookups per hour per server address.
    github_token: SecretStr | None = None

    # Protects the free AI quotas.
    max_repos_per_user: int = 5

    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """One shared Settings object. Tests clear the cache to change it."""
    return Settings()
