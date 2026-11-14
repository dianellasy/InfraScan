"""Seed one London Bridge POC geolocation job from `inference/data/uploads/multi.png`.

The PNG is converted in memory to a JPEG copy (the original file is only
read) and submitted through the same validation, `DatabaseInfraScanService`,
`InferenceJobProcessor`, and persistence used for API submissions, with the
shared `LONDON_BRIDGE_POC` configuration. Detections come from running the
model on those submitted bytes.

Repeated runs reuse the existing seed job instead of creating another one.
A seed job is a job whose input is exactly these JPEG bytes under the
reserved seed filename. Concurrent runs are serialized by a PostgreSQL
advisory lock.

Run from `backend/` with `DATABASE_URL` set and the schema at `alembic upgrade head`:

    python -m app.seed_poc                # run the model in this process
    python -m app.seed_poc --via-worker   # publish to the Celery worker and wait
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterator
from uuid import UUID

import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import UploadFile

from app.adapters.ports import InferenceQueueAdapter
from app.api.v1.router import api_router
from app.api.v1.routes.inference import _ContractError, _validate_image
from app.db import create_db_engine, create_session_factory
from app.geolocation import LONDON_BRIDGE_POC, POC_GEOLOCATION_ENV
from app.geolocation.poc import poc_geolocation_enabled
from app.models import InferenceJob, InferenceJobStatus, InputMedia
from app.schemas import DetectionDTO, InferenceJobDTO, InferenceSubmissionMetadata
from app.services.contracts import MediaKind
from app.services.inference_jobs import DatabaseInfraScanService
from app.services.inference_processing import InferenceJobProcessor, InferenceRunner

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = BACKEND_DIR / "inference" / "data" / "uploads" / "multi.png"
SEED_FILENAME = "infrascan-poc-seed-london-bridge-multi.jpg"
# Fixed placeholder; multi.png carries no capture time.
SEED_CAPTURED_AT = "2026-11-13T12:00:00+00:00"
SEED_JPEG_QUALITY = 95
# Arbitrary constant identifying the seed command's advisory lock.
SEED_LOCK_KEY = 0x1F5EED_0001
API_PREFIX = "/api/v1"
_TERMINAL = {InferenceJobStatus.COMPLETED.value, InferenceJobStatus.FAILED.value}


class SeedError(RuntimeError):
    pass


def jpeg_copy(source: Path) -> bytes:
    """Encode an RGB JPEG copy of `source` in memory; `source` is only read."""
    with Image.open(source) as image:
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=SEED_JPEG_QUALITY)
    return buffer.getvalue()


class _CollectingQueue:
    """Queue adapter for in-process seeding: the job is processed right here."""

    def __init__(self) -> None:
        self.job_ids: list[UUID] = []

    def enqueue(self, job_id: UUID) -> None:
        self.job_ids.append(job_id)


@dataclass(frozen=True)
class SeedUrls:
    api_base_url: str
    frontend_url: str

    def media_url(self, kind: MediaKind, media_id: UUID) -> str:
        name = "get_evidence_media" if kind == "evidence" else "get_input_media"
        path = api_router.url_path_for(name, media_id=str(media_id))
        return f"{self.api_base_url}{API_PREFIX}{path}"

    def job_url(self, job_id: UUID) -> str:
        return f"{self.api_base_url}{API_PREFIX}/jobs/{job_id}"

    def frontend_job_url(self, job_id: UUID) -> str:
        return f"{self.frontend_url}/?job={job_id}"


@dataclass(frozen=True)
class SeedResult:
    job: InferenceJobDTO
    detections: list[DetectionDTO]
    created: bool


@contextmanager
def _seed_lock(engine: sa.Engine) -> Iterator[None]:
    with engine.connect() as connection:
        connection.execute(sa.text("SELECT pg_advisory_lock(:key)"), {"key": SEED_LOCK_KEY})
        connection.commit()
        try:
            yield
        finally:
            connection.execute(
                sa.text("SELECT pg_advisory_unlock(:key)"), {"key": SEED_LOCK_KEY}
            )
            connection.commit()


def find_seed_jobs(
    session_factory: sessionmaker[Session], jpeg: bytes
) -> list[tuple[UUID, str]]:
    """Existing seed jobs for these exact bytes, newest first."""
    with session_factory() as session:
        rows = session.execute(
            sa.select(InferenceJob.job_id, InferenceJob.status)
            .join(InferenceJob.input_media)
            .where(InputMedia.filename == SEED_FILENAME, InputMedia.data == jpeg)
            .order_by(InferenceJob.created_at.desc(), InferenceJob.job_id)
        ).all()
    return [(row.job_id, row.status) for row in rows]


def _pick_existing(jobs: list[tuple[UUID, str]]) -> tuple[UUID, str] | None:
    for wanted in (
        InferenceJobStatus.COMPLETED.value,
        InferenceJobStatus.RUNNING.value,
        InferenceJobStatus.PENDING.value,
    ):
        for job_id, status in jobs:
            if status == wanted:
                return job_id, status
    return None


async def _submit(service: DatabaseInfraScanService, jpeg: bytes) -> UUID:
    upload = UploadFile(file=BytesIO(jpeg), filename=SEED_FILENAME, size=len(jpeg))
    metadata = InferenceSubmissionMetadata.model_validate(
        {
            "captured_at": SEED_CAPTURED_AT,
            "latitude": LONDON_BRIDGE_POC.source.latitude,
            "longitude": LONDON_BRIDGE_POC.source.longitude,
        }
    )
    try:
        await _validate_image(upload)
    except _ContractError as exc:
        raise SeedError(f"seed image rejected by submission validation: {exc.message}") from exc
    accepted = await service.submit_inference(upload, metadata)
    return accepted.job_id


def _default_runner() -> InferenceRunner:
    from inference.celery.tasks import run_v0_mvp_inference

    return run_v0_mvp_inference


def seed(
    engine: sa.Engine,
    *,
    source: Path = DEFAULT_SOURCE,
    via_worker: bool = False,
    queue: InferenceQueueAdapter | None = None,
    run_inference: Callable[[], InferenceRunner] = _default_runner,
    urls: SeedUrls | None = None,
    timeout_s: float = 180.0,
    log: Callable[[str], None] = print,
) -> SeedResult:
    session_factory = create_session_factory(engine)
    jpeg = jpeg_copy(source)
    collector = _CollectingQueue()
    if via_worker and queue is None:
        from app.adapters.celery_queue import CeleryInferenceQueue
        from inference.celery.celery_app import celery_app

        queue = CeleryInferenceQueue(celery_app)
    service = DatabaseInfraScanService(
        session_factory,
        queue if via_worker else collector,
        poc_geolocation=LONDON_BRIDGE_POC,
    )

    with _seed_lock(engine):
        existing = _pick_existing(find_seed_jobs(session_factory, jpeg))
        created = existing is None
        if existing is None:
            job_id = asyncio.run(_submit(service, jpeg))
            status = InferenceJobStatus.PENDING.value
            log(f"Submitted new seed job {job_id}")
        else:
            job_id, status = existing
            log(f"Reusing existing seed job {job_id} ({status})")

        if status not in _TERMINAL:
            if via_worker:
                log("Waiting for the inference worker...")
                deadline = time.monotonic() + timeout_s
                while status not in _TERMINAL and time.monotonic() < deadline:
                    time.sleep(1.0)
                    status = asyncio.run(service.get_job(job_id)).status
                if status not in _TERMINAL:
                    raise SeedError(
                        f"seed job {job_id} is still {status} after {timeout_s:g}s; "
                        "is the worker running?"
                    )
            elif status == InferenceJobStatus.PENDING.value:
                log("Running detection and geolocation in this process...")
                InferenceJobProcessor(session_factory, run_inference()).process(job_id)
            else:
                raise SeedError(
                    f"seed job {job_id} is already running in another process "
                    "(or was stranded); rerun with --via-worker to wait for it"
                )

    job = asyncio.run(service.get_job(job_id, urls))
    page = asyncio.run(service.list_job_detections(job_id, 1, 100, urls))
    return SeedResult(job=job, detections=page.items, created=created)


def _format_report(result: SeedResult, urls: SeedUrls) -> str:
    job = result.job
    located = [d for d in result.detections if d.location is not None]
    lines = [
        "",
        f"Seed job {job.job_id} ({'created' if result.created else 'reused, no new records'})",
        f"  status: {job.status}, model: {job.model_version}, "
        f"detections: {len(result.detections)}, with demo location: {len(located)}",
    ]
    if job.geolocation is not None:
        source = job.geolocation.source
        lines.append(
            f"  source GPS (ASSUMED, {job.geolocation.mode}): {source.name} "
            f"{source.latitude:.6f}, {source.longitude:.6f}"
        )
    for d in result.detections:
        where = (
            f"{d.location.latitude:.7f}, {d.location.longitude:.7f} (demo estimate)"
            if d.location is not None
            else f"no location ({d.location_status})"
        )
        lines.append(
            f"    {d.classification:<18} {d.confidence:6.1%}  {d.severity:<8}  {where}"
        )
    lines += [
        "",
        f"  Job:             {urls.job_url(job.job_id)}",
        f"  Detections:      {urls.job_url(job.job_id)}/detections",
        f"  Annotated image: {job.annotated_image_url or '(not available)'}",
        f"  Frontend:        {urls.frontend_job_url(job.job_id)}",
        "",
        "multi.png is treated as though captured at London Bridge with the sample",
        "camera setup; these coordinates do not establish its real capture location.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--via-worker",
        action="store_true",
        help="publish to the configured Celery broker and wait for the worker",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("INFRASCAN_PUBLIC_API_URL", "http://localhost:8000"),
    )
    parser.add_argument(
        "--frontend-url",
        default=os.environ.get("INFRASCAN_FRONTEND_URL", "http://localhost:5173"),
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    if not poc_geolocation_enabled():
        print(
            f"warning: {POC_GEOLOCATION_ENV} is not enabled for this process; the seed "
            "job still uses the London Bridge POC setup, but the API will not apply "
            "it to ordinary submissions until it is enabled there.",
            file=sys.stderr,
        )

    urls = SeedUrls(args.api_base_url.rstrip("/"), args.frontend_url.rstrip("/"))
    engine = create_db_engine()
    try:
        result = seed(
            engine,
            source=args.source,
            via_worker=args.via_worker,
            urls=urls,
            timeout_s=args.timeout,
        )
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(_format_report(result, urls))
    return 0 if result.job.status == InferenceJobStatus.COMPLETED.value else 1


if __name__ == "__main__":
    sys.exit(main())
