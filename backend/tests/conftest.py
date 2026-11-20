from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.db import create_db_engine, create_session_factory
from a119_seed_support import generate_seed
from postgres_support import disposable_postgres, run_alembic

# `issue_types` is reference data inserted by its migration, so it is not truncated.
APPLICATION_TABLES = (
    "detection_records",
    "evidence_media",
    "inference_jobs",
    "input_media",
    "issues",
)


@pytest.fixture
def disposable_postgres_url() -> Iterator[str]:
    with disposable_postgres() as url:
        yield url


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[sa.Engine]:
    """One disposable cluster per test module, upgraded with Alembic (not create_all)."""
    with disposable_postgres() as url:
        upgrade = run_alembic("upgrade", "head", database_url=url)
        assert upgrade.returncode == 0, upgrade.stderr
        engine = create_db_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture(scope="session")
def a119_seed(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., tuple[Path, dict]]:
    """Build (and cache per session) a synthetic seed clip; tests must not modify it."""
    cache: dict[tuple, tuple[Path, dict]] = {}

    def make(scenario: str = "standard", **options) -> tuple[Path, dict]:
        key = (scenario, tuple(sorted(options.items())))
        if key not in cache:
            cache[key] = generate_seed(tmp_path_factory.mktemp("a119"), scenario, **options)
        return cache[key]

    return make


@pytest.fixture
def session_factory(migrated_engine: sa.Engine) -> sessionmaker[Session]:
    with migrated_engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {', '.join(APPLICATION_TABLES)}"))
    return create_session_factory(migrated_engine)
