"""Per-process database engine for Celery workers.

The engine is created lazily inside the process that uses it. Prefork children
call `dispose_engine(close=False)` on start so they never reuse pooled
connections inherited from the parent.
"""

from __future__ import annotations

import threading

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import create_db_engine, create_session_factory

_lock = threading.Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    global _engine, _session_factory
    with _lock:
        if _session_factory is None:
            _engine = create_db_engine()
            _session_factory = create_session_factory(_engine)
        return _session_factory


def dispose_engine(close: bool = True) -> None:
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose(close=close)
        _engine = None
        _session_factory = None
