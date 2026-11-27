import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.r2_storage import R2MediaStorageAdapter
from app.api.errors import register_exception_handlers
from app.api.upload_limit import UploadLimitMiddleware
from app.api.v1.router import api_router
from app.db import create_db_engine, create_session_factory
from app.services.inference_jobs import DatabaseInfraScanService

logger = logging.getLogger(__name__)


def build_queue(session_factory, media_storage):
    """Celery (RabbitMQ) locally and in Docker; in-process after the response on Vercel.

    Imports are local so the Vercel function never loads Celery.
    """
    if os.environ.get("VERCEL"):
        from app.adapters.background_queue import WaitUntilInferenceQueue
        from app.services.inference_processing import InferenceJobProcessor
        from inference.service import DEFAULT_WEIGHT_PATH, inference_with_annotation

        def build_processor() -> InferenceJobProcessor:
            if media_storage is None:
                raise RuntimeError("Object storage is not configured")
            return InferenceJobProcessor(
                session_factory,
                run_inference=lambda data, filename: inference_with_annotation(
                    DEFAULT_WEIGHT_PATH, data, filename=filename
                ),
                media_storage=media_storage,
            )

        return WaitUntilInferenceQueue(build_processor)

    from app.adapters.celery_queue import CeleryInferenceQueue
    from inference.celery.celery_app import celery_app

    return CeleryInferenceQueue(celery_app)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Schema changes are applied with `alembic upgrade head`, never at startup.
    if not os.environ.get("DATABASE_URL", "").strip():
        logger.warning("DATABASE_URL is not set; the InfraScan service is not configured")
        yield
        return

    engine = create_db_engine()
    try:
        media_storage = R2MediaStorageAdapter()
    except KeyError as exc:
        # Reads of legacy rows still work; submissions return 503 until fixed.
        logger.error("Object storage is not configured: missing %s", exc.args[0])
        media_storage = None
    session_factory = create_session_factory(engine)
    app.state.infrascan_service = DatabaseInfraScanService(
        session_factory,
        build_queue(session_factory, media_storage),
        media_storage,
    )
    try:
        yield
    finally:
        app.state.infrascan_service = None
        engine.dispose()


DEFAULT_CORS_ALLOWED_ORIGINS = "http://localhost:5173"


def cors_allowed_origins(raw: str | None) -> list[str]:
    """Exact origins from a comma-separated list; wildcards are rejected."""
    value = DEFAULT_CORS_ALLOWED_ORIGINS if raw is None or not raw.strip() else raw
    origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
    if "*" in origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must list exact origins, not '*'")
    return origins


app = FastAPI(title="InfraScan API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins(os.environ.get("CORS_ALLOWED_ORIGINS")),
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(UploadLimitMiddleware)
register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")


@app.get("/test")
def firstEndpoint() -> str:
    return "Hello InfraScan"
