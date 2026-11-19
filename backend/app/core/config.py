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
    # Test generation (feature D) uses the default key with a stronger model.
    groq_model: str = "openai/gpt-oss-120b"
    groq_model_summaries: str = "openai/gpt-oss-20b"
    llm_timeout_seconds: float = 20.0
    # Fallback provider for the `default` budget only (test generation, vulnerability checks).
    # Check https://ai.google.dev/gemini-api/docs/models for the names your key can use.
    gemini_model: str = "gemini-2.5-flash"
    # Retries and the circuit breaker (feature F). A busy or failing provider is retried after the
    # waits below, then the next provider is tried. One call never takes much longer than the
    # total budget. After `llm_breaker_failures` failed calls in a row a provider is skipped for
    # `llm_breaker_seconds` (kept in memory, so a server restart clears it).
    llm_retry_waits: tuple[float, ...] = (1.0, 3.0)
    llm_total_budget_seconds: float = 60.0
    llm_breaker_failures: int = 3
    llm_breaker_seconds: float = 60.0
    # Daily allowance (UTC day), counted in tokens (input + output) from `llm_usage`.
    # When one is used up, test generation ends with "no result, allowance used", never a fake pass.
    llm_daily_tokens_per_repo: int = 150_000
    llm_daily_tokens_per_user: int = 300_000
    # File summaries (C6) have their own, smaller allowance, counted apart from the one above so
    # they can never use up what test generation needs.
    llm_daily_summary_tokens_per_repo: int = 40_000
    llm_daily_summary_tokens_per_user: int = 80_000


@lru_cache
def get_settings() -> Settings:
    """One shared Settings object. Tests clear the cache to change it."""
    return Settings()
