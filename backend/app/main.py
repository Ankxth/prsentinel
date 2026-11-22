"""The FastAPI application.

Run it locally from the backend/ folder:

    uvicorn app.main:app --reload

On Render the start command is:

    alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    agent,
    context,
    findings,
    generate,
    health,
    me,
    mutation,
    repair,
    repos,
    results,
    run_view,
    runs,
    triage,
)
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="PRSentinel", version=__version__)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.frontend_origin.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(me.router)
    app.include_router(repos.router)
    app.include_router(runs.router)
    app.include_router(context.router)
    app.include_router(generate.router)
    app.include_router(results.router)
    app.include_router(repair.router)
    app.include_router(mutation.router)
    app.include_router(run_view.router)
    app.include_router(findings.router)
    app.include_router(triage.router)
    app.include_router(agent.router)
    return app


app = create_app()
