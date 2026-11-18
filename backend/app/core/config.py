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
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None

    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """One shared Settings object. Tests clear the cache to change it."""
    return Settings()
