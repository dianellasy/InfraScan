"""Direct browser uploads: signed PUT URL, then a JSON job submission.

Uses the real PostgreSQL-backed service with an in-memory object store that
records what was signed, so the full route -> validation -> job row -> queue
path runs without R2.
"""

from __future__ import annotations

from io import BytesIO
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.models import InferenceJob, InputMedia
from app.services.inference_jobs import DatabaseInfraScanService
from app.services.object_keys import direct_upload_key
from auth_support import CITIZEN_A, CITIZEN_B, auth_headers

VALID_METADATA = {
    "captured_at": "2026-11-10T14:30:00-08:00",
    "latitude": 33.8823,
    "longitude": -117.8851,
}


def make_jpeg(width: int = 64, height: int = 48) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(60, 90, 120)).save(buffer, format="JPEG")
    return buffer.getvalue()


def make_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48)).save(buffer, format="PNG")
    return buffer.getvalue()


class DirectUploadStorage:
    """In-memory stand-in for R2 that also signs (fake) upload URLs."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.signed: list[tuple[str, str, int, int]] = []
        self.deletes: list[str] = []

    def presign_put(self, key: str, content_type: str, content_length: int, expires_in: int) -> str:
        self.signed.append((key, content_type, content_length, expires_in))
        return f"https://r2.example/{key}?signature=test"

    def size(self, key: str) -> int | None:
        data = self.objects.get(key)
        return None if data is None else len(data)

    def download_to_file(self, key: str, target, max_bytes: int) -> int:
        data = self.objects[key]
        assert len(data) <= max_bytes
        target.write(data)
        return len(data)

    def put(self, data: bytes, object_key: str, content_type: str) -> None:
        self.objects[object_key] = data

    def put_file(self, source, object_key: str, content_type: str) -> None:
        source.seek(0)
        self.objects[object_key] = source.read()

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def delete(self, key: str) -> None:
        self.deletes.append(key)
        self.objects.pop(key, None)


class RecordingQueue:
    def __init__(self) -> None:
        self.job_ids: list[UUID] = []

    def enqueue(self, job_id: UUID) -> None:
        self.job_ids.append(job_id)


@pytest.fixture
def storage() -> DirectUploadStorage:
    return DirectUploadStorage()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def app(session_factory: sessionmaker[Session], storage, queue) -> FastAPI:
    test_app = FastAPI()
    test_app.state.infrascan_service = DatabaseInfraScanService(session_factory, queue, storage)
    register_exception_handlers(test_app)
    test_app.include_router(api_router, prefix="/api/v1")
    return test_app


def client_for(app: FastAPI, user: UUID = CITIZEN_A) -> TestClient:
    return TestClient(app, headers=auth_headers(user))


def request_upload(client: TestClient, content_type: str, size: int):
    return client.post(
        "/api/v1/inference/uploads", json={"content_type": content_type, "size_bytes": size}
    )


def submit_upload(client: TestClient, upload_id, content_type="image/jpeg", metadata=None):
    return client.post(
        "/api/v1/inference/jobs",
        json={
            "upload_id": str(upload_id),
            "content_type": content_type,
            "filename": "road.jpg",
            "metadata": VALID_METADATA if metadata is None else metadata,
        },
    )


def upload(client: TestClient, storage: DirectUploadStorage, data: bytes, content_type="image/jpeg"):
    """Steps 1 and 2: get a ticket, then 'PUT' the bytes where it points."""
    response = request_upload(client, content_type, len(data))
    assert response.status_code == 201, response.text
    ticket = response.json()
    key = storage.signed[-1][0]
    storage.objects[key] = data
    return UUID(ticket["upload_id"]), key


def job_count(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(sa.select(sa.func.count()).select_from(InferenceJob))


def test_ticket_signs_owner_scoped_key_for_exact_type_and_size(app, storage) -> None:
    with client_for(app) as client:
        response = request_upload(client, "image/jpeg", 1234)

    assert response.status_code == 201
    body = response.json()
    upload_id = UUID(body["upload_id"])
    assert body["method"] == "PUT"
    assert body["headers"] == {"Content-Type": "image/jpeg"}
    assert body["upload_url"].startswith("https://r2.example/")
    assert storage.signed == [
        (direct_upload_key(CITIZEN_A, upload_id, "image/jpeg"), "image/jpeg", 1234, 900)
    ]


@pytest.mark.parametrize(
    ("content_type", "size"), [("image/jpeg", 5_000_001), ("video/mp4", 1_000_000_001)]
)
def test_ticket_rejects_files_over_the_contract_limit(app, storage, content_type, size) -> None:
    with client_for(app) as client:
        response = request_upload(client, content_type, size)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert storage.signed == []


def test_uploaded_jpeg_becomes_a_pending_job_and_is_published(
    app, storage, queue, session_factory
) -> None:
    with client_for(app) as client:
        upload_id, key = upload(client, storage, make_jpeg())
        response = submit_upload(client, upload_id)

    assert response.status_code == 202, response.text
    job_id = UUID(response.json()["job_id"])
    assert queue.job_ids == [job_id]
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        media = session.get(InputMedia, job.input_media_id)
        assert job.user_id == CITIZEN_A
        assert float(job.latitude) == pytest.approx(33.8823)
        assert media.media_id == upload_id
        assert media.object_storage_key == key
        assert media.content_type == "image/jpeg"
        assert media.data is None
    assert key in storage.objects  # the worker reads it from here


def test_same_upload_cannot_start_a_second_job_or_be_deleted(
    app, storage, queue, session_factory
) -> None:
    with client_for(app) as client:
        upload_id, key = upload(client, storage, make_jpeg())
        assert submit_upload(client, upload_id).status_code == 202
        again = submit_upload(client, upload_id, metadata={"captured_at": "bad"})

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "UPLOAD_ALREADY_SUBMITTED"
    assert key in storage.objects
    assert job_count(session_factory) == 1


def test_missing_upload_is_reported_without_creating_a_job(app, queue, session_factory) -> None:
    with client_for(app) as client:
        response = submit_upload(client, uuid4())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_NOT_FOUND"
    assert queue.job_ids == []
    assert job_count(session_factory) == 0


def test_another_user_cannot_start_a_job_from_my_upload(app, storage, session_factory) -> None:
    with client_for(app, CITIZEN_A) as owner:
        upload_id, key = upload(owner, storage, make_jpeg())
    with client_for(app, CITIZEN_B) as other:
        response = submit_upload(other, upload_id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_NOT_FOUND"
    assert key in storage.objects
    assert job_count(session_factory) == 0


def test_invalid_bytes_are_rejected_and_the_object_deleted(app, storage, session_factory) -> None:
    with client_for(app) as client:
        upload_id, key = upload(client, storage, make_png())
        response = submit_upload(client, upload_id)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_IMAGE_FORMAT"
    assert key not in storage.objects
    assert job_count(session_factory) == 0


def test_bad_metadata_is_rejected_and_the_object_deleted(app, storage, session_factory) -> None:
    with client_for(app) as client:
        upload_id, key = upload(client, storage, make_jpeg())
        response = submit_upload(client, upload_id, metadata={**VALID_METADATA, "latitude": 91})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_COORDINATE"
    assert key not in storage.objects
    assert job_count(session_factory) == 0


def test_declared_video_holding_a_jpeg_is_a_type_mismatch(app, storage, session_factory) -> None:
    with client_for(app) as client:
        upload_id, key = upload(client, storage, make_jpeg(), content_type="video/mp4")
        response = submit_upload(client, upload_id, content_type="video/mp4")

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "CONTENT_TYPE_MISMATCH"
    assert key not in storage.objects
    assert job_count(session_factory) == 0


def test_object_larger_than_the_limit_is_rejected_and_deleted(app, storage, session_factory) -> None:
    with client_for(app) as client:
        upload_id, key = upload(client, storage, make_jpeg())
        storage.objects[key] = b"\xff\xd8" + b"0" * 5_000_000  # grew after signing
        response = submit_upload(client, upload_id)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert key not in storage.objects
    assert job_count(session_factory) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"upload_id": "not-a-uuid", "content_type": "image/jpeg", "metadata": {}},
        {"upload_id": str(uuid4()), "content_type": "image/png", "metadata": {}},
        {"upload_id": str(uuid4()), "content_type": "image/jpeg"},
        {"upload_id": str(uuid4()), "content_type": "image/jpeg", "metadata": {}, "extra": 1},
    ],
)
def test_malformed_json_submission_is_rejected(app, body) -> None:
    with client_for(app) as client:
        response = client.post("/api/v1/inference/jobs", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_unconfigured_storage_makes_direct_uploads_unavailable(session_factory) -> None:
    app = FastAPI()
    app.state.infrascan_service = DatabaseInfraScanService(session_factory, RecordingQueue(), None)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    with client_for(app) as client:
        response = request_upload(client, "image/jpeg", 100)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SUBMISSION_DEPENDENCY_UNAVAILABLE"


# --- Vercel path: the job is processed in the API after the response ---


class AsyncQueue:
    """Like WaitUntilInferenceQueue: scheduled on the event loop, run later."""

    def __init__(self, error: Exception | None = None) -> None:
        self.scheduled: list[UUID] = []
        self.error = error

    async def enqueue_async(self, job_id: UUID) -> None:
        if self.error is not None:
            raise self.error
        self.scheduled.append(job_id)


def test_async_queue_receives_the_committed_job(session_factory, storage) -> None:
    queue = AsyncQueue()
    app = FastAPI()
    app.state.infrascan_service = DatabaseInfraScanService(session_factory, queue, storage)
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    with client_for(app) as client:
        upload_id, _ = upload(client, storage, make_jpeg())
        response = submit_upload(client, upload_id)

    assert response.status_code == 202
    assert queue.scheduled == [UUID(response.json()["job_id"])]


def test_scheduling_failure_marks_the_job_failed(session_factory, storage) -> None:
    app = FastAPI()
    app.state.infrascan_service = DatabaseInfraScanService(
        session_factory, AsyncQueue(RuntimeError("no context")), storage
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    with client_for(app) as client:
        upload_id, _ = upload(client, storage, make_jpeg())
        response = submit_upload(client, upload_id)

    assert response.status_code == 503
    with session_factory() as session:
        (status,) = session.execute(sa.select(InferenceJob.status)).one()
    assert status == "failed"
