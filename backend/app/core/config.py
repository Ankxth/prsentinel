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

    # Server-only admin key (the SECRET key, never the publishable one). Only used to remove a
    # user's Supabase login when they delete their account. Optional: without it their data
    # is still deleted, but the login record stays in Supabase.
    supabase_secret_key: SecretStr | None = None

    # Optional GitHub token (no special permissions needed). Without one, GitHub's
    # open API allows only 60 lookups per hour per server address.
    github_token: SecretStr | None = None

    # The Action asks GitHub for an identity token for this audience; we only accept that audience.
    github_oidc_audience: str = "prsentinel"

    # Signs the short-lived run token handed to the Action after it starts a run. Make it a long
    # random string (for example: python -c "import secrets; print(secrets.token_urlsafe(48))").
    run_token_secret: SecretStr | None = None
    run_token_ttl_seconds: int = 3600

    # Sent to the Action as a hint of how many changed functions to submit.
    max_functions_per_run: int = 50

    # Protects the free AI quotas.
    max_repos_per_user: int = 5

    # Context map limits (feature C). Past these the map is marked partial, never silently cut.
    max_context_files: int = 500
    max_context_batch_files: int = 100
    max_context_upload_bytes: int = 2_000_000

    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    # AI file summaries (C6) use their OWN Groq key so they can never use up the quota that test
    # generation and the vulnerability checks depend on. There is no fallback to GROQ_API_KEY:
    # without this key, summaries are simply switched off.
    groq_api_key_summaries: SecretStr | None = None
    # llama-3.1-8b-instant was retired on Groq (404 model_not_found, 2026-11-19). Check
    # https://console.groq.com/docs/models for what your key can use.
    groq_model: str = "openai/gpt-oss-20b"
    groq_model_summaries: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = 20.0


@lru_cache
def get_settings() -> Settings:
    """One shared Settings object. Tests clear the cache to change it."""
    return Settings()
