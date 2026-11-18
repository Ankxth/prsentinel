"""Migrations must apply cleanly to an empty database and be reversible.

Uses a throwaway SQLite file so no real database is needed. (Postgres-only
features, such as RLS policies, get their own tests when they are added.)
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.core.config import BACKEND_DIR, get_settings
from app.db.session import normalize_database_url


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


@pytest.fixture
def sqlite_url(tmp_path: Path, monkeypatch) -> str:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def test_single_head():
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1, f"migrations have branched: {heads}"


def test_upgrade_then_downgrade(sqlite_url):
    config = alembic_config()
    command.upgrade(config, "head")
    tables = inspect(create_engine(sqlite_url)).get_table_names()
    assert "alembic_version" in tables

    command.downgrade(config, "base")
    tables = inspect(create_engine(sqlite_url)).get_table_names()
    assert "alembic_version" not in tables or tables == ["alembic_version"]


def test_missing_database_url_gives_clear_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "app.core.config.Settings.model_config",
        {**get_settings().model_config, "env_file": None},
    )
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        command.upgrade(alembic_config(), "head")
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "given,expected",
    [
        ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_normalize_database_url(given, expected):
    assert normalize_database_url(given) == expected
