import json
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.api.v1.router import api_router
from app.schemas import InferenceJobAccepted
from app.services.contracts import SubmissionDependencyUnavailableError


JOB_ID = UUID("7b101a70-ec2b-4c8a-a728-272fff4fd7b8")
VALID_METADATA = {
    "captured_at": "2026-11-10T14:30:00-08:00",
    "latitude": 33.8823,
    "longitude": -117.8851,
}


def make_jpeg(width: int = 64, height: int = 48) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(60, 90, 120)).save(
        buffer, format="JPEG"
    )
    return buffer.getvalue()


def make_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48), color=(60, 90, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeSubmissionService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def submit_inference(self, file, metadata) -> InferenceJobAccepted:
        start_position = file.file.tell()
        content = await file.read()
        self.calls.append(
            {
                "start_position": start_position,
                "content": content,
                "filename": file.filename,
                "metadata": metadata,
            }
        )
        return InferenceJobAccepted(
            job_id=JOB_ID,
            status="pending",
            submitted_at=datetime(2026, 11, 10, 22, 30, 1, tzinfo=UTC),
        )


class UnavailableSubmissionService(FakeSubmissionService):
    async def submit_inference(self, file, metadata) -> InferenceJobAccepted:
        raise SubmissionDependencyUnavailableError


class BrokenSubmissionService(FakeSubmissionService):
    async def submit_inference(self, file, metadata) -> InferenceJobAccepted:
        raise RuntimeError("sensitive internal detail")


@pytest.fixture
def service() -> FakeSubmissionService:
    return FakeSubmissionService()


@pytest.fixture
def app(service: FakeSubmissionService) -> FastAPI:
    test_app = FastAPI()
    test_app.state.infrascan_service = service
    test_app.include_router(api_router, prefix="/api/v1")
    return test_app


@pytest.fixture
def client(app: FastAPI):
    with TestClient(app) as test_client:
        yield test_client


def submit(
    client: TestClient,
    image: bytes | None = None,
    metadata: object = VALID_METADATA,
    *,
    filename: str = "road.jpg",
):
    parts = []
    if image is not None:
        parts.append(("file", (filename, image, "image/jpeg")))
    if metadata is not None:
        raw_metadata = metadata if isinstance(metadata, str) else json.dumps(metadata)
        parts.append(("metadata", (None, raw_metadata)))
    return client.post("/api/v1/inference/jobs", files=parts)


def assert_error(response, status_code: int, code: str, field: str | None) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"].get("field") == field
    assert "message" in body["error"]
    assert "detail" not in body


@pytest.mark.parametrize("dimensions", [(1920, 1080), (1080, 1920), (320, 240)])
def test_accepts_landscape_portrait_and_smaller_jpegs(
    client: TestClient,
    service: FakeSubmissionService,
    dimensions: tuple[int, int],
) -> None:
    image = make_jpeg(*dimensions)

    response = submit(client, image)

    assert response.status_code == 202
    assert len(service.calls) == 1
    assert service.calls[0]["content"] == image


@pytest.mark.parametrize("dimensions", [(4032, 3024), (3024, 4032)])
def test_accepts_dimension_boundaries(
    client: TestClient,
    service: FakeSubmissionService,
    dimensions: tuple[int, int],
) -> None:
    response = submit(client, make_jpeg(*dimensions))

    assert response.status_code == 202
    assert len(service.calls) == 1


def test_valid_submission_calls_service_once_with_rewound_original_file(
    client: TestClient,
    service: FakeSubmissionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1.routes import inference

    def fail_if_dispatched(*args, **kwargs):
        raise AssertionError("production submission must not dispatch Celery directly")

    monkeypatch.setattr(inference, "_dispatch_and_await", fail_if_dispatched)
    image = make_jpeg()

    response = submit(client, image)

    assert response.status_code == 202
    assert response.json() == {
        "job_id": str(JOB_ID),
        "status": "pending",
        "submitted_at": "2026-11-10T22:30:01Z",
    }
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["start_position"] == 0
    assert call["content"] == image
    assert call["filename"] == "road.jpg"
    assert call["metadata"].captured_at == datetime(
        2026, 11, 10, 22, 30, tzinfo=UTC
    )


def test_accepts_exactly_5_000_000_bytes(
    client: TestClient, service: FakeSubmissionService
) -> None:
    jpeg = make_jpeg()
    image = jpeg + b"\0" * (5_000_000 - len(jpeg))

    response = submit(client, image)

    assert response.status_code == 202
    assert service.calls[0]["content"] == image


def test_rejects_file_over_5_000_000_bytes(
    client: TestClient, service: FakeSubmissionService
) -> None:
    jpeg = make_jpeg()
    image = jpeg + b"\0" * (5_000_001 - len(jpeg))

    response = submit(client, image)

    assert_error(response, 413, "FILE_TOO_LARGE", "file")
    assert service.calls == []


@pytest.mark.parametrize("dimensions", [(4033, 1000), (3025, 3025), (3500, 3500)])
def test_rejects_dimensions_over_either_limit(
    client: TestClient,
    service: FakeSubmissionService,
    dimensions: tuple[int, int],
) -> None:
    response = submit(client, make_jpeg(*dimensions))

    assert_error(response, 422, "INVALID_IMAGE_DIMENSIONS", "file")
    assert service.calls == []


def test_rejects_png_content_named_jpg(
    client: TestClient, service: FakeSubmissionService
) -> None:
    response = submit(client, make_png(), filename="image.jpg")

    assert_error(response, 415, "UNSUPPORTED_IMAGE_FORMAT", "file")
    assert service.calls == []


def test_rejects_truncated_jpeg(
    client: TestClient, service: FakeSubmissionService
) -> None:
    response = submit(client, make_jpeg()[:-20])

    assert_error(response, 422, "INVALID_IMAGE", "file")
    assert service.calls == []


def test_rejects_decompression_bomb(
    client: TestClient,
    service: FakeSubmissionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    response = submit(client, make_jpeg(100, 100))

    assert_error(response, 422, "INVALID_IMAGE", "file")
    assert service.calls == []


def test_uses_content_not_declared_mime_type(
    client: TestClient, service: FakeSubmissionService
) -> None:
    parts = [
        ("file", ("image.png", make_jpeg(), "image/png")),
        ("metadata", (None, json.dumps(VALID_METADATA))),
    ]

    response = client.post("/api/v1/inference/jobs", files=parts)

    assert response.status_code == 202
    assert len(service.calls) == 1


@pytest.mark.parametrize(
    ("parts", "status_code", "code", "field"),
    [
        (
            [("metadata", (None, json.dumps(VALID_METADATA)))],
            422,
            "MISSING_FILE",
            "file",
        ),
        (
            [
                ("file", ("one.jpg", make_jpeg(), "image/jpeg")),
                ("file", ("two.jpg", make_jpeg(), "image/jpeg")),
                ("metadata", (None, json.dumps(VALID_METADATA))),
            ],
            400,
            "MULTIPLE_FILES",
            "file",
        ),
        (
            [
                ("file", ("one.jpg", make_jpeg(), "image/jpeg")),
                ("metadata", (None, json.dumps(VALID_METADATA))),
                ("metadata", (None, json.dumps(VALID_METADATA))),
            ],
            400,
            "DUPLICATE_METADATA",
            "metadata",
        ),
        (
            [
                ("file", ("one.jpg", make_jpeg(), "image/jpeg")),
                ("extra_file", ("two.jpg", make_jpeg(), "image/jpeg")),
                ("metadata", (None, json.dumps(VALID_METADATA))),
            ],
            400,
            "UNEXPECTED_MULTIPART_FIELD",
            "extra_file",
        ),
        (
            [
                ("file", ("one.jpg", make_jpeg(), "image/jpeg")),
                ("metadata", (None, json.dumps(VALID_METADATA))),
                ("note", (None, "unexpected")),
            ],
            400,
            "UNEXPECTED_MULTIPART_FIELD",
            "note",
        ),
    ],
)
def test_rejects_invalid_multipart_structure(
    client: TestClient,
    service: FakeSubmissionService,
    parts,
    status_code: int,
    code: str,
    field: str,
) -> None:
    response = client.post("/api/v1/inference/jobs", files=parts)

    assert_error(response, status_code, code, field)
    assert service.calls == []


def test_rejects_empty_file(
    client: TestClient, service: FakeSubmissionService
) -> None:
    response = submit(client, b"")

    assert_error(response, 422, "EMPTY_FILE", "file")
    assert service.calls == []


def test_rejects_missing_metadata(
    client: TestClient, service: FakeSubmissionService
) -> None:
    response = submit(client, make_jpeg(), metadata=None)

    assert_error(response, 422, "MISSING_METADATA", "metadata")
    assert service.calls == []


def test_rejects_non_multipart_request(
    client: TestClient, service: FakeSubmissionService
) -> None:
    response = client.post("/api/v1/inference/jobs", json=VALID_METADATA)

    assert_error(response, 415, "UNSUPPORTED_REQUEST_MEDIA_TYPE", None)
    assert service.calls == []


def test_rejects_malformed_multipart_encoding(
    client: TestClient, service: FakeSubmissionService
) -> None:
    response = client.post(
        "/api/v1/inference/jobs",
        content=b"not a multipart body",
        headers={"content-type": "multipart/form-data"},
    )

    assert_error(response, 400, "INVALID_MULTIPART", None)
    assert service.calls == []


@pytest.mark.parametrize(
    ("metadata", "code", "field"),
    [
        ("{not json", "INVALID_METADATA_JSON", "metadata"),
        ([], "INVALID_METADATA", "metadata"),
        (
            {"latitude": 1, "longitude": 2},
            "MISSING_METADATA_FIELD",
            "metadata.captured_at",
        ),
        (
            {"captured_at": None, "latitude": 1, "longitude": 2},
            "INVALID_CAPTURED_AT",
            "metadata.captured_at",
        ),
        (
            {
                **VALID_METADATA,
                "user_id": "client-must-not-supply-this",
            },
            "UNKNOWN_METADATA_FIELD",
            "metadata.user_id",
        ),
        (
            {
                "captured_at": "2026-11-10T14:30:00",
                "latitude": 1,
                "longitude": 2,
            },
            "INVALID_CAPTURED_AT",
            "metadata.captured_at",
        ),
        (
            {"captured_at": 1_799_000_000, "latitude": 1, "longitude": 2},
            "INVALID_CAPTURED_AT",
            "metadata.captured_at",
        ),
        (
            {**VALID_METADATA, "latitude": None},
            "INVALID_COORDINATE",
            "metadata.latitude",
        ),
        (
            {**VALID_METADATA, "latitude": True},
            "INVALID_COORDINATE",
            "metadata.latitude",
        ),
        (
            {**VALID_METADATA, "latitude": "33.8"},
            "INVALID_COORDINATE",
            "metadata.latitude",
        ),
        (
            {**VALID_METADATA, "latitude": 90.1},
            "INVALID_COORDINATE",
            "metadata.latitude",
        ),
        (
            {**VALID_METADATA, "longitude": -180.1},
            "INVALID_COORDINATE",
            "metadata.longitude",
        ),
        (
            '{"captured_at":"2026-11-10T14:30:00Z","latitude":NaN,"longitude":0}',
            "INVALID_METADATA_JSON",
            "metadata",
        ),
    ],
)
def test_rejects_invalid_metadata(
    client: TestClient,
    service: FakeSubmissionService,
    metadata,
    code: str,
    field: str,
) -> None:
    response = submit(client, make_jpeg(), metadata)

    assert_error(response, 422, code, field)
    assert service.calls == []


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(-90, -180), (90, 180), (0, 0), (33.8823, -117.8851)],
)
def test_accepts_coordinate_endpoints_and_numbers(
    client: TestClient,
    service: FakeSubmissionService,
    latitude: int | float,
    longitude: int | float,
) -> None:
    metadata = {
        "captured_at": "2000-01-01T01:02:03+01:00",
        "latitude": latitude,
        "longitude": longitude,
    }

    response = submit(client, make_jpeg(), metadata)

    assert response.status_code == 202
    received = service.calls[0]["metadata"]
    assert received.latitude == float(latitude)
    assert received.longitude == float(longitude)
    assert received.captured_at == datetime(2000, 1, 1, 0, 2, 3, tzinfo=UTC)


def test_missing_service_returns_contract_503() -> None:
    test_app = FastAPI()
    test_app.include_router(api_router, prefix="/api/v1")

    with TestClient(test_app) as test_client:
        response = submit(test_client, make_jpeg())

    assert_error(response, 503, "SERVICE_UNAVAILABLE", None)


@pytest.mark.parametrize(
    ("service", "status_code", "code"),
    [
        (
            UnavailableSubmissionService(),
            503,
            "SUBMISSION_DEPENDENCY_UNAVAILABLE",
        ),
        (BrokenSubmissionService(), 500, "INTERNAL_ERROR"),
    ],
)
def test_service_failures_use_safe_error_envelope(
    service, status_code: int, code: str
) -> None:
    test_app = FastAPI()
    test_app.state.infrascan_service = service
    test_app.include_router(api_router, prefix="/api/v1")

    with TestClient(test_app) as test_client:
        response = submit(test_client, make_jpeg())

    assert_error(response, status_code, code, None)
    assert "sensitive internal detail" not in response.text
