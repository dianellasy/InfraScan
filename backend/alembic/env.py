from __future__ import annotations

from logging.config import fileConfig
import os
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import create_engine, pool

ALEMBIC_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ALEMBIC_DIR.parent
for search_dir in (BACKEND_DIR, ALEMBIC_DIR):
    if str(search_dir) not in sys.path:
        sys.path.insert(0, str(search_dir))

from app.models import Base  # noqa: E402
from runtime import include_name, resolve_database_url  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (`--sql`), emitting DDL without connecting."""
    raw_url = os.environ.get("DATABASE_URL")
    configure_kwargs: dict[str, object] = {
        "target_metadata": target_metadata,
        "literal_binds": True,
        "dialect_opts": {"paramstyle": "named"},
        "include_name": include_name,
        "compare_type": True,
        "compare_server_default": True,
    }
    if raw_url is not None and raw_url.strip():
        configure_kwargs["url"] = resolve_database_url(raw_url)
    else:
        configure_kwargs["dialect_name"] = "postgresql"

    context.configure(**configure_kwargs)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an engine built from `DATABASE_URL`."""
    connectable = create_engine(
        resolve_database_url(),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
