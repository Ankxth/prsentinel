"""Health check. Used by the uptime pinger and by the dashboard."""

from fastapi import APIRouter

from app import __version__
from app.core.config import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "prsentinel-backend",
        "version": __version__,
        "env": get_settings().app_env,
    }
