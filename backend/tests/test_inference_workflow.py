"""Database-backed inference workflow tests.

Worker tests use `tests/fixtures/v0_mvp_multi_png_output.json`, the exact
output of the shipped v0-mvp weights on `inference/data/uploads/multi.png`,
with a mocked runner. The real model path is covered in
`test_real_model_pipeline.py` when ultralytics is installed.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import logging
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.celery_queue import CeleryInferenceQueue
from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.db import create_db_engine, create_session_factory
from app.models import (
    DetectionRecord,
    EvidenceMedia,
    InferenceJob,
    InferenceJobStatus,
    InputMedia,
)
from app.repositories import DetectionRecordRepository, InferenceJobRepository
from app.services.inference_jobs import DatabaseInfraScanService, detection_to_dto
from app.services.inference_processing import InferenceJobProcessor
from inference.celery.celery_app import PROCESS_INFERENCE_JOB_TASK

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v0_mvp_multi_png_output.json"
ANNOTATED_JPEG = b"\xff\xd8\xff\xe0annotated-evidence\xff\xd9"
STARTED_AT = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)
VALID_METADATA = {
    "captured_at": "2026-11-10T14:30:00-08:00",
    "latitude": 33.8823,
    "longitude": -117.8851,
}


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def make_jpeg(width: int = 64, height: int = 48) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(60, 90, 120)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def v0_output() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class StepClock:
    """Deterministic UTC clock advancing one second per call."""

    def __init__(self, start: datetime = STARTED_AT) -> None:
        self.calls: list[datetime] = []
        self._next = start

    def __call__(self) -> datetime:
        value = self._next
        self._next += timedelta(seconds=1)
        self.calls.append(value)
        return value


class RecordingQueue:
    """Records enqueue calls and what was committed at publication time."""

    def __init__(self, session_factory: sessionmaker[Session], error: Exception | None = None):
        self._session_factory = session_factory
        self._error = error
        self.job_ids: list[UUID] = []
        self.committed_at_publish: list[bool] = []
        self.ran_on_event_loop: list[bool] = []

    def enqueue(self, job_id: UUID) -> None:
        self.ran_on_event_loop.append(_on_event_loop())
        self.job_ids.append(job_id)
        # A separate session only sees rows that are already committed.
        with self._session_factory() as session:
            job = session.get(InferenceJob, job_id)
            self.committed_at_publish.append(
                job is not None and session.get(InputMedia, job.input_media_id) is not None
            )
        if self._error is not None:
            raise self._error


class FakeCeleryApp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def send_task(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))


def build_client(service: DatabaseInfraScanService) -> TestClient:
    app = FastAPI()
    app.state.infrascan_service = service
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app)


def submit(client: TestClient, image: bytes | None = None, filename: str = "road.jpg"):
    return client.post(
        "/api/v1/inference/jobs",
        files=[
            ("file", (filename, image or make_jpeg(), "image/png")),
            ("metadata", (None, json.dumps(VALID_METADATA))),
        ],
    )


def count_rows(session_factory: sessionmaker[Session], model: type) -> int:
    with session_factory() as session:
        return session.scalar(sa.select(sa.func.count()).select_from(model))


def create_pending_job(session_factory: sessionmaker[Session], image: bytes = b"jpeg") -> UUID:
    with session_factory() as session, session.begin():
        job = InferenceJob(
            input_media=InputMedia(data=image, content_type="image/jpeg", filename="road.jpg"),
            captured_at=datetime(2026, 11, 10, 22, 30, tzinfo=UTC),
            latitude=Decimal("33.8823"),
            longitude=Decimal("-117.8851"),
        )
        session.add(job)
        session.flush()
        return job.job_id


# --- Queue adapter (no database needed) ---


def test_celery_queue_publishes_only_the_job_id_string() -> None:
    celery = FakeCeleryApp()
    job_id = uuid4()

    CeleryInferenceQueue(celery).enqueue(job_id)

    assert celery.calls == [(PROCESS_INFERENCE_JOB_TASK, (), {"args": [str(job_id)]})]


def test_database_unavailable_at_submission_does_not_enqueue(tmp_path: Path) -> None:
    engine = create_db_engine(f"postgresql://postgres@/missing?host={tmp_path}")
    queue = RecordingQueue(create_session_factory(engine))
    try:
        with build_client(DatabaseInfraScanService(create_session_factory(engine), queue)) as client:
            response = submit(client)
    finally:
        engine.dispose()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SUBMISSION_DEPENDENCY_UNAVAILABLE"
    assert queue.job_ids == []


# --- Submission ---


def test_submission_commits_media_and_job_before_enqueue(
    session_factory: sessionmaker[Session],
) -> None:
    queue = RecordingQueue(session_factory)
    image = make_jpeg()

    with build_client(DatabaseInfraScanService(session_factory, queue)) as client:
        response = submit(client, image)

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"job_id", "status", "submitted_at"}
    job_id = UUID(body["job_id"])
    assert body["status"] == "pending"

    assert queue.job_ids == [job_id]
    assert queue.committed_at_publish == [True]
    assert queue.ran_on_event_loop == [False]

    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert job.status == "pending"
        assert job.captured_at == datetime(2026, 11, 10, 22, 30, tzinfo=UTC)
        assert job.latitude == Decimal("33.8823")
        assert job.longitude == Decimal("-117.8851")
        assert job.model_version is None
        assert job.started_at is None
        assert body["submitted_at"].endswith("Z")
        assert datetime.fromisoformat(body["submitted_at"]) == job.created_at
        # Content type comes from validation, not the client's declared image/png.
        assert job.input_media.data == image
        assert job.input_media.content_type == "image/jpeg"
        assert job.input_media.filename == "road.jpg"


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (OperationalError("INSERT", {}, Exception("connection lost")), 503, "SUBMISSION_DEPENDENCY_UNAVAILABLE"),
        (RuntimeError("bug"), 500, "INTERNAL_ERROR"),
    ],
)
def test_job_insert_failure_rolls_back_media_and_does_not_enqueue(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    def fail(self, media, metadata, geolocation=None):
        assert media.media_id is not None  # media row was already flushed
        raise error

    monkeypatch.setattr(InferenceJobRepository, "create_pending", fail)
    queue = RecordingQueue(session_factory)

    with build_client(DatabaseInfraScanService(session_factory, queue)) as client:
        response = submit(client)

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert queue.job_ids == []
    assert count_rows(session_factory, InputMedia) == 0
    assert count_rows(session_factory, InferenceJob) == 0


def test_queue_publication_error_returns_dependency_error_and_fails_job(
    session_factory: sessionmaker[Session],
) -> None:
    queue = RecordingQueue(session_factory, error=ConnectionRefusedError("broker down"))

    with build_client(DatabaseInfraScanService(session_factory, queue)) as client:
        response = submit(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SUBMISSION_DEPENDENCY_UNAVAILABLE"
    assert len(queue.job_ids) == 1
    with session_factory() as session:
        job = session.get(InferenceJob, queue.job_ids[0])
        assert job.status == "failed"
        assert job.input_media is not None


# --- Worker processing ---


def test_worker_persists_detections_with_shared_evidence_and_exact_values(
    session_factory: sessionmaker[Session], v0_output: dict
) -> None:
    image = make_jpeg()
    job_id = create_pending_job(session_factory, image)
    seen_inputs = []

    def runner(image_bytes: bytes, filename: str | None):
        seen_inputs.append((image_bytes, filename))
        return copy.deepcopy(v0_output), ANNOTATED_JPEG

    clock = StepClock()
    status = InferenceJobProcessor(session_factory, runner, clock=clock).process(job_id)

    assert status == InferenceJobStatus.COMPLETED
    assert seen_inputs == [(image, "road.jpg")]
    started_at, detected_at, completed_at = clock.calls

    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert job.status == "completed"
        assert job.model_version == "v0-mvp"
        assert job.started_at == started_at
        assert job.completed_at == completed_at
        assert job.input_media.data == image

        assert len(job.evidence_media) == 1
        evidence = job.evidence_media[0]
        assert evidence.data == ANNOTATED_JPEG
        assert evidence.content_type == "image/jpeg"

        records = sorted(job.detection_records, key=lambda r: r.confidence, reverse=True)
        expected = v0_output["detections"]
        assert len(records) == len(expected) == 5
        for record, detection in zip(records, expected):
            assert record.issue_type == detection["class_ids"]
            assert record.confidence == detection["confidence"]
            assert record.severity == detection["severity"]
            # Exact equality: fractional pixel coordinates must round-trip.
            assert record.bounding_box == detection["bbox_xyxy"]
            assert record.evidence_media == evidence.media_id
            assert record.issue_id is None
            assert record.location is None
            assert record.frame_timestamp is None
            assert record.detected_at == detected_at


def test_worker_runs_inference_after_committing_running_state(
    session_factory: sessionmaker[Session], migrated_engine: sa.Engine, v0_output: dict
) -> None:
    job_id = create_pending_job(session_factory)
    observed = {}

    def runner(image_bytes: bytes, filename: str | None):
        with session_factory() as session:
            job = session.get(InferenceJob, job_id)
            observed["status"] = job.status
            observed["started_at"] = job.started_at
        with migrated_engine.connect() as conn:
            observed["idle_in_transaction"] = conn.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
                )
            )
        return v0_output, ANNOTATED_JPEG

    InferenceJobProcessor(session_factory, runner).process(job_id)

    assert observed["status"] == "running"
    assert observed["started_at"] is not None
    assert observed["idle_in_transaction"] == 0


def test_zero_detection_inference_completes_with_no_records(
    session_factory: sessionmaker[Session], v0_output: dict
) -> None:
    job_id = create_pending_job(session_factory)
    empty_output = {**v0_output, "detections": []}

    status = InferenceJobProcessor(
        session_factory, lambda image, name: (empty_output, ANNOTATED_JPEG)
    ).process(job_id)

    assert status == InferenceJobStatus.COMPLETED
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert job.status == "completed"
        assert job.model_version == "v0-mvp"
        assert job.completed_at is not None
        assert job.detection_records == []
        assert len(job.evidence_media) == 1


def test_inference_failure_marks_job_failed_and_logs_job_id(
    session_factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    job_id = create_pending_job(session_factory)

    def runner(image_bytes: bytes, filename: str | None):
        raise RuntimeError("model crashed")

    with caplog.at_level(logging.ERROR):
        status = InferenceJobProcessor(session_factory, runner).process(job_id)

    assert status == InferenceJobStatus.FAILED
    assert any(str(job_id) in r.getMessage() and r.exc_info for r in caplog.records)
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert job.status == "failed"
        assert job.started_at is not None
        assert job.completed_at is None
        assert job.model_version is None
    assert count_rows(session_factory, EvidenceMedia) == 0
    assert count_rows(session_factory, DetectionRecord) == 0


def test_malformed_model_output_marks_job_failed(
    session_factory: sessionmaker[Session], v0_output: dict
) -> None:
    job_id = create_pending_job(session_factory)
    bad = copy.deepcopy(v0_output)
    bad["detections"][2]["bbox_xyxy"] = [1.0, 2.0, 3.0]

    status = InferenceJobProcessor(session_factory, lambda i, n: (bad, ANNOTATED_JPEG)).process(job_id)

    assert status == InferenceJobStatus.FAILED
    assert count_rows(session_factory, DetectionRecord) == 0


def test_result_persistence_failure_rolls_back_all_outputs(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    v0_output: dict,
) -> None:
    job_id = create_pending_job(session_factory)

    def fail(self, *args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("connection lost"))

    monkeypatch.setattr(DetectionRecordRepository, "add_many", fail)

    status = InferenceJobProcessor(
        session_factory, lambda i, n: (v0_output, ANNOTATED_JPEG)
    ).process(job_id)

    assert status == InferenceJobStatus.FAILED
    # The evidence row was flushed in the same transaction and must be gone.
    assert count_rows(session_factory, EvidenceMedia) == 0
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert job.status == "failed"
        assert job.model_version is None


def test_worker_skips_unknown_and_non_pending_jobs(
    session_factory: sessionmaker[Session], v0_output: dict
) -> None:
    calls = []

    def runner(image: bytes, name: str | None):
        calls.append(name)
        return v0_output, ANNOTATED_JPEG

    processor = InferenceJobProcessor(session_factory, runner)
    assert processor.process(uuid4()) is None

    job_id = create_pending_job(session_factory)
    assert processor.process(job_id) == InferenceJobStatus.COMPLETED
    assert processor.process(job_id) is None
    assert len(calls) == 1
    assert count_rows(session_factory, DetectionRecord) == 5


# --- Tracking endpoints ---


def test_end_to_end_submit_process_and_track(
    session_factory: sessionmaker[Session], v0_output: dict
) -> None:
    queue = RecordingQueue(session_factory)
    service = DatabaseInfraScanService(session_factory, queue)

    with build_client(service) as client:
        accepted = submit(client).json()
        job_id = UUID(accepted["job_id"])

        pending = client.get(f"/api/v1/jobs/{job_id}")
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"
        assert client.get(f"/api/v1/jobs/{job_id}/detections").json()["items"] == []

        InferenceJobProcessor(
            session_factory, lambda i, n: (v0_output, ANNOTATED_JPEG)
        ).process(queue.job_ids[0])

        job = client.get(f"/api/v1/jobs/{job_id}").json()
        assert job["job_id"] == str(job_id)
        assert job["status"] == "completed"
        assert datetime.fromisoformat(job["submitted_at"]) == datetime.fromisoformat(
            accepted["submitted_at"]
        )
        assert job["started_at"] is not None
        assert job["completed_at"] is not None
        assert job["model_version"] == "v0-mvp"
        assert job["metadata"]["latitude"] == 33.8823
        assert job["metadata"]["longitude"] == -117.8851

        page_one = client.get(
            f"/api/v1/jobs/{job_id}/detections", params={"page": 1, "page_size": 2}
        ).json()
        page_three = client.get(
            f"/api/v1/jobs/{job_id}/detections", params={"page": 3, "page_size": 2}
        ).json()

    assert (page_one["total"], page_one["page"], page_one["page_size"]) == (5, 1, 2)
    assert len(page_one["items"]) == 2
    assert len(page_three["items"]) == 1

    first = page_one["items"][0]
    top = v0_output["detections"][0]
    assert first["job_id"] == str(job_id)
    assert first["classification"] == "Pothole"
    assert first["issue_type"] == 3
    assert first["severity"] == top["severity"]
    assert first["confidence"] == top["confidence"]
    assert first["bounding_box"] == top["bbox_xyxy"]
    assert first["evidence_media"] is not None
    assert first["issue_id"] is None
    assert first["location"] is None
    assert first["latitude"] is None and first["longitude"] is None
    assert first["frame_timestamp"] is None
    assert first["road_id"] is None
    # TIMESTAMPTZ is read back in the server's zone; the API normalizes to UTC.
    assert first["detected_at"].endswith("Z")
    assert job["completed_at"].endswith("Z")


def test_successful_zero_detection_job_lists_empty_page(
    session_factory: sessionmaker[Session], v0_output: dict
) -> None:
    job_id = create_pending_job(session_factory)
    InferenceJobProcessor(
        session_factory, lambda i, n: ({**v0_output, "detections": []}, ANNOTATED_JPEG)
    ).process(job_id)

    with build_client(DatabaseInfraScanService(session_factory, RecordingQueue(session_factory))) as client:
        response = client.get(f"/api/v1/jobs/{job_id}/detections")
        job = client.get(f"/api/v1/jobs/{job_id}").json()

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}
    assert job["status"] == "completed"


@pytest.mark.parametrize("suffix", ["", "/detections"])
def test_unknown_job_returns_404(session_factory: sessionmaker[Session], suffix: str) -> None:
    with build_client(DatabaseInfraScanService(session_factory, RecordingQueue(session_factory))) as client:
        response = client.get(f"/api/v1/jobs/{uuid4()}{suffix}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "JOB_NOT_FOUND",
            "message": "The inference job was not found.",
            "field": "job_id",
        }
    }


@pytest.mark.parametrize(
    ("issue_type", "classification"),
    [(0, "Longitudinal crack"), (1, "Transverse crack"), (2, "Alligator crack"), (3, "Pothole")],
)
def test_detection_dto_maps_issue_type_to_classification(
    issue_type: int, classification: str
) -> None:
    record = DetectionRecord(
        detection_id=uuid4(),
        job_id=uuid4(),
        issue_type=issue_type,
        confidence=0.5,
        severity="minor",
        bounding_box=[1.25, 2.5, 3.75, 4.0],
        detected_at=STARTED_AT,
        location={"latitude": 33.9, "longitude": -117.9},
    )

    dto = detection_to_dto(record)

    assert dto.classification == classification
    assert dto.issue_type == issue_type
    assert dto.bounding_box == [1.25, 2.5, 3.75, 4.0]
    assert (dto.latitude, dto.longitude) == (33.9, -117.9)


# --- Application wiring ---


def test_app_without_database_url_keeps_contract_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import main

    with TestClient(main.app) as client:
        response = submit(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_app_with_database_url_wires_database_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:1/infrascan")
    import main

    with TestClient(main.app) as client:
        assert isinstance(main.app.state.infrascan_service, DatabaseInfraScanService)
        not_implemented = client.get("/api/v1/issues")

    assert not_implemented.status_code == 501
    assert not_implemented.json()["error"]["code"] == "NOT_IMPLEMENTED"
