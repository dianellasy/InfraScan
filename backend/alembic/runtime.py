from __future__ import annotations

from collections.abc import MutableMapping
import os

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# Celery's database result backend manages its own metadata tables in the same
# PostgreSQL database during local development. Exclude them so Alembic never
# attempts to manage or drop Celery tables during `check` or `--autogenerate`.
CELERY_RESULT_BACKEND_TABLES: frozenset[str] = frozenset(
    {
        "celery_taskmeta",
        "celery_tasksetmeta",
    }
)


def include_name(
    name: str | None,
    type_: str,
    parent_names: MutableMapping[str, str | None],
) -> bool:
    del parent_names
    if type_ == "table" and name in CELERY_RESULT_BACKEND_TABLES:
        return False
    return True


def resolve_database_url(raw_url: str | None = None) -> str:
    """Resolve `DATABASE_URL` and normalize it to the installed `psycopg` driver.

    Never embeds raw connection strings in exception messages so credentials
    cannot leak into logs or tracebacks.
    """
    candidate = raw_url if raw_url is not None else os.environ.get("DATABASE_URL")
    if candidate is None or not candidate.strip():
        raise RuntimeError(
            "DATABASE_URL environment variable is required to run Alembic against a database."
        )

    try:
        parsed = make_url(candidate.strip())
    except (ArgumentError, ValueError):
        raise RuntimeError(
            "DATABASE_URL is not a valid SQLAlchemy database URL."
        ) from None

    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg")
    elif parsed.drivername != "postgresql+psycopg":
        raise RuntimeError(
            "Unsupported database driver configured in DATABASE_URL. "
            "Supply a PostgreSQL URL using 'postgresql://', 'postgres://', "
            "or 'postgresql+psycopg://'."
        )

    return parsed.render_as_string(hide_password=False)
