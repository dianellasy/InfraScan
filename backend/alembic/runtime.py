from __future__ import annotations

from collections.abc import MutableMapping

from app.db import resolve_database_url

__all__ = ["CELERY_RESULT_BACKEND_TABLES", "include_name", "resolve_database_url"]

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
