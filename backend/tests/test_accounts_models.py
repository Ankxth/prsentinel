"""The users/repos models and the migration must agree, and the relations must behave."""

import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.db.models.accounts import Repo, User
from tests.test_migrations import alembic_config


@pytest.fixture
def engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'accounts.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    command.upgrade(alembic_config(), "head")
    eng = create_engine(url)

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):  # SQLite ignores foreign keys unless asked
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    yield eng
    get_settings.cache_clear()


def make_user(login="alice", github_id=1):
    return User(id=uuid.uuid4(), github_login=login, github_id=github_id)


def make_repo(user, github_id=100, name="demo"):
    return Repo(
        owner_user_id=user.id,
        github_id=github_id,
        github_owner=user.github_login,
        github_name=name,
        default_branch="main",
        settings={},
    )


def test_migration_matches_models(engine):
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        assert compare_metadata(ctx, Base.metadata) == []


def test_tables_created(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"users", "repos"} <= tables


def test_repo_defaults_are_safe(engine):
    with Session(engine) as s:
        user = make_user()
        s.add(user)
        s.flush()
        repo = make_repo(user)
        s.add(repo)
        s.commit()
        assert repo.llm_opt_in is False  # no code goes to the AI until the user agrees
        assert repo.is_public is True
        assert repo.full_name == "alice/demo"


def test_same_github_repo_cannot_be_added_twice(engine):
    with Session(engine) as s:
        a, b = make_user("alice", 1), make_user("bob", 2)
        s.add_all([a, b])
        s.flush()
        s.add(make_repo(a, github_id=100))
        s.commit()
        s.add(make_repo(b, github_id=100, name="other"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_deleting_user_deletes_their_repos(engine):
    with Session(engine) as s:
        user = make_user()
        s.add(user)
        s.flush()
        s.add(make_repo(user))
        s.commit()
        s.delete(user)
        s.commit()
        assert s.query(Repo).count() == 0


def test_downgrade_removes_tables(engine):
    command.downgrade(alembic_config(), "0001")
    assert "repos" not in inspect(engine).get_table_names()
