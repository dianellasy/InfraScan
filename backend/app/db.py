from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker


def resolve_database_url(raw_url: str | None = None) -> str:
    """Resolve `DATABASE_URL` and normalize it to the installed `psycopg` driver.

    Never embeds raw connection strings in exception messages so credentials
    cannot leak into logs or tracebacks.
    """
    candidate = raw_url if raw_url is not None else os.environ.get("DATABASE_URL")
    if candidate is None or not candidate.strip():
        raise RuntimeError(
            "DATABASE_URL environment variable is required to connect to the database."
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


def create_db_engine(raw_url: str | None = None) -> Engine:
    """Create a pooled engine. Does not connect until first use.

    On Vercel (VERCEL=1) many short-lived function instances share the
    database, which is reached through a transaction-mode pooler such as
    Supabase's (port 6543). Each instance then keeps a small pool, and
    psycopg's automatic prepared statements are off because a transaction
    pooler cannot route them back to the same server connection.
    """
    if os.environ.get("VERCEL"):
        return create_engine(
            resolve_database_url(raw_url),
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=3,
            pool_recycle=300,
            connect_args={"prepare_threshold": None},
        )
    return create_engine(resolve_database_url(raw_url), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Committed values stay readable after the session closes, so callers can
    # build DTOs without holding a session or transaction open.
    return sessionmaker(engine, expire_on_commit=False)
