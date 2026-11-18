"""Import every model module here so Alembic autogenerate can find the tables."""

from app.db.models import accounts, runs  # noqa: F401
