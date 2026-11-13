import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.celery_queue import CeleryInferenceQueue
from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.db import create_db_engine, create_session_factory
from app.services.inference_jobs import DatabaseInfraScanService
from inference.celery.celery_app import celery_app

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Schema changes are applied with `alembic upgrade head`, never at startup.
    if not os.environ.get("DATABASE_URL", "").strip():
        logger.warning("DATABASE_URL is not set; the InfraScan service is not configured")
        yield
        return

    engine = create_db_engine()
    app.state.infrascan_service = DatabaseInfraScanService(
        create_session_factory(engine), CeleryInferenceQueue(celery_app)
    )
    try:
        yield
    finally:
        app.state.infrascan_service = None
        engine.dispose()


app = FastAPI(title="InfraScan API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)
register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")


@app.get("/test")
def firstEndpoint() -> str:
    return "Hello InfraScan"
