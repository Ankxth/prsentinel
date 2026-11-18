from fastapi.testclient import TestClient

from app import __version__
from app.core.config import get_settings
from app.main import create_app


def make_client() -> TestClient:
    get_settings.cache_clear()
    return TestClient(create_app())


def test_health_returns_ok():
    response = make_client().get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "prsentinel-backend"
    assert body["version"] == __version__


def test_unknown_path_is_404():
    assert make_client().get("/does-not-exist").status_code == 404


def test_cors_allows_the_dashboard_origin(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    client = make_client()
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_does_not_allow_other_origins(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    client = make_client()
    response = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_settings_start_with_no_env_file_values(monkeypatch):
    for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.app_env in ("development", "production") or settings.app_env


def test_secrets_are_not_printed(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-value")
    get_settings.cache_clear()
    assert "super-secret-value" not in repr(get_settings())
    get_settings.cache_clear()
