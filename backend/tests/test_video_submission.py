"""HTTP contract for A119 Mini 2 MP4 submissions (no database or R2)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from a119_seed_support import ffmpeg_clip, requires_ffmpeg
from app.api.upload_limit import UploadLimitMiddleware
from app.api.v1.router import api_router
from app.schemas import ResolvedVideoSubmission
from test_inference_submission import FakeSubmissionService, assert_error

OFFSET = {"camera_utc_offset_minutes": -480}


@pytest.fixture
def service() -> FakeSubmissionService:
    return FakeSubmissionService()


def build_app(service, max_bytes: int | None = None) -> FastAPI:
    app = FastAPI()
    app.state.infrascan_service = service
    app.include_router(api_router, prefix="/api/v1")
    if max_bytes is not None:
        app.add_middleware(UploadLimitMiddleware, max_bytes=max_bytes)
    return app


@pytest.fixture
def client(service):
    with TestClient(build_app(service)) as test_client:
        yield test_client


def submit(
    client: TestClient,
    content: bytes,
    metadata: object = OFFSET,
    filename: str = "clip.MP4",
    mime: str = "video/mp4",
):
    raw = metadata if isinstance(metadata, str) else json.dumps(metadata)
    return client.post(
        "/api/v1/inference/jobs",
        files=[("file", (filename, content, mime)), ("metadata", (None, raw))],
    )


def make_jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 48), color=(60, 90, 120)).save(buffer, format="JPEG")
    return buffer.getvalue()


# --- Accepted MP4 ---


@requires_ffmpeg
@pytest.mark.parametrize(
    ("filename", "mime"),
    [
        ("20261120123000_000001.MP4", "video/mp4"),
        ("clip.jpg", "image/jpeg"),
        ("upload", "application/octet-stream"),
    ],
)
def test_mp4_is_detected_from_bytes_and_resolved_from_first_fix(
    client, service, a119_seed, filename: str, mime: str
) -> None:
    path, manifest = a119_seed("cold-start")
    content = path.read_bytes()
    response = submit(client, content, filename=filename, mime=mime)
    assert response.status_code == 202, response.json()

    call = service.calls[0]
    assert call["start_position"] == 0
    assert call["content"] == content
    metadata = call["metadata"]
    assert isinstance(metadata, ResolvedVideoSubmission)
    expected = manifest["expected_first_fix"]
    assert metadata.camera_utc_offset_minutes == -480
    assert metadata.captured_at == datetime(2026, 11, 20, 20, 30, 3, tzinfo=UTC)
    assert metadata.captured_at.isoformat().replace("+00:00", "Z") == expected["recorded_at_utc"]
    assert metadata.latitude == pytest.approx(expected["latitude"])
    assert metadata.longitude == pytest.approx(expected["longitude"])


@requires_ffmpeg
def test_audio_is_optional(client, service, a119_seed) -> None:
    path, _ = a119_seed("standard", audio=True)
    assert submit(client, path.read_bytes()).status_code == 202


# --- Metadata ---


@requires_ffmpeg
@pytest.mark.parametrize(
    ("metadata", "field"),
    [
        ({}, "metadata.camera_utc_offset_minutes"),
        ({"camera_utc_offset_minutes": 10}, "metadata.camera_utc_offset_minutes"),
        ({"camera_utc_offset_minutes": 900}, "metadata.camera_utc_offset_minutes"),
        ({"camera_utc_offset_minutes": "-480"}, "metadata.camera_utc_offset_minutes"),
        ({"camera_utc_offset_minutes": True}, "metadata.camera_utc_offset_minutes"),
        ({"camera_utc_offset_minutes": -480.0}, "metadata.camera_utc_offset_minutes"),
        ({"camera_utc_offset_minutes": None}, "metadata.camera_utc_offset_minutes"),
        ({**OFFSET, "latitude": 33.8}, "metadata.latitude"),
        ({**OFFSET, "captured_at": "2026-11-20T12:30:00Z"}, "metadata.captured_at"),
        ({**OFFSET, "camera_model": "A119"}, "metadata.camera_model"),
    ],
)
def test_invalid_video_metadata(client, service, a119_seed, metadata: dict, field: str) -> None:
    path, _ = a119_seed("standard")
    assert_error(submit(client, path.read_bytes(), metadata), 422, "INVALID_METADATA", field)
    assert service.calls == []


def test_image_metadata_on_non_mp4_bytes_keeps_the_jpeg_contract(client, service) -> None:
    metadata = {"captured_at": "2026-11-10T14:30:00-08:00", "latitude": 33.8823, "longitude": -117.8851}
    response = submit(client, make_jpeg(), metadata, filename="clip.mp4")
    assert response.status_code == 202
    assert not isinstance(service.calls[0]["metadata"], ResolvedVideoSubmission)


def test_video_metadata_with_non_mp4_bytes_is_unsupported(client, service) -> None:
    assert_error(submit(client, make_jpeg()), 415, "UNSUPPORTED_MEDIA_FORMAT", "file")
    assert service.calls == []


def test_empty_mp4_upload(client, service) -> None:
    assert_error(submit(client, b""), 422, "EMPTY_FILE", "file")


# --- Video and GPS contract ---


@requires_ffmpeg
def test_mp4_without_gps_is_missing_gps_track(client, service, a119_seed) -> None:
    path, _ = a119_seed("no-gps")
    assert_error(submit(client, path.read_bytes()), 422, "MISSING_GPS_TRACK", "file")
    assert service.calls == []


@requires_ffmpeg
def test_all_void_gps_is_missing_gps_track(client, service, a119_seed) -> None:
    path, _ = a119_seed("standard")
    all_void = path.read_bytes().replace(b"ANW\0", b"V00\0")
    assert all_void.count(b"V00\0") == 4
    assert_error(submit(client, all_void), 422, "MISSING_GPS_TRACK", "file")


@requires_ffmpeg
def test_truncated_mp4_is_invalid_video(client, a119_seed) -> None:
    path, _ = a119_seed("standard")
    assert_error(submit(client, path.read_bytes()[:40_000]), 422, "INVALID_VIDEO", "file")


@requires_ffmpeg
@pytest.mark.parametrize(
    ("args", "status_code", "code"),
    [
        (["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=1", "-c:v", "libx265",
          "-x265-params", "log-level=none", "-tag:v", "hvc1", "-f", "mp4"], 415, "UNSUPPORTED_MEDIA_FORMAT"),
        (["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=60:duration=1", "-c:v", "libx264", "-f", "mp4"],
         422, "INVALID_VIDEO_FRAME_RATE"),
        (["-f", "lavfi", "-i", "testsrc2=size=2560x1440:rate=1:duration=1", "-c:v", "libx264", "-f", "mp4"],
         422, "INVALID_VIDEO_DIMENSIONS"),
        (["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=1", "-c:v", "libx264", "-f", "mov"],
         415, "UNSUPPORTED_MEDIA_FORMAT"),
    ],
    ids=["h265", "60fps", "2k", "mov"],
)
def test_video_outside_the_contract(client, service, tmp_path: Path, args, status_code, code) -> None:
    clip = ffmpeg_clip(tmp_path / "clip.bin", *args)
    assert_error(submit(client, clip.read_bytes()), status_code, code, "file")
    assert service.calls == []


def test_over_600_second_duration_is_rejected(client, monkeypatch) -> None:
    from inference import video

    monkeypatch.setattr(
        video, "probe_video",
        lambda path: video.evaluate_probe(
            {"format": {"duration": "601.0"}, "streams": [{
                "index": 0, "codec_type": "video", "codec_name": "h264", "profile": "High",
                "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "avg_frame_rate": "30/1",
            }]}
        ),
    )
    content = b"\0\0\0\x18ftypisom\0\0\0\0isom" + bytes(64)
    assert_error(submit(client, content), 422, "INVALID_VIDEO_DURATION", "file")


def test_missing_media_tools_return_503(client, monkeypatch) -> None:
    from inference import video

    def missing(path):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(video, "probe_video", missing)
    content = b"\0\0\0\x18ftypisom\0\0\0\0isom" + bytes(64)
    response = submit(client, content)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SUBMISSION_DEPENDENCY_UNAVAILABLE"


# --- Request size limit ---


def test_request_limit_with_content_length(service) -> None:
    with TestClient(build_app(service, max_bytes=10_000)) as client:
        response = submit(client, b"\0\0\0\x18ftypisom" + bytes(20_000))
    assert_error(response, 413, "FILE_TOO_LARGE", "file")
    assert "1,000,000,000" in response.json()["error"]["message"]
    assert service.calls == []


def test_request_limit_without_content_length(service) -> None:
    boundary = "infrascanboundary"
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n"
        f"{json.dumps(OFFSET)}\r\n--{boundary}\r\nContent-Disposition: form-data; "
        f"name=\"file\"; filename=\"clip.MP4\"\r\nContent-Type: video/mp4\r\n\r\n"
    ).encode()

    async def body():
        yield head + b"\0\0\0\x18ftypisom"
        for _ in range(10):
            yield bytes(4096)
        yield f"\r\n--{boundary}--\r\n".encode()

    async def run():
        transport = httpx.ASGITransport(app=build_app(service, max_bytes=10_000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v1/inference/jobs",
                content=body(),
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )

    response = asyncio.run(run())
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert_error(response, 413, "FILE_TOO_LARGE", "file")
    assert service.calls == []


def test_video_byte_limit_is_one_billion() -> None:
    from app.api.upload_limit import MAX_REQUEST_BYTES
    from inference.video import MAX_VIDEO_BYTES

    assert MAX_VIDEO_BYTES == 1_000_000_000
    assert MAX_VIDEO_BYTES < MAX_REQUEST_BYTES <= MAX_VIDEO_BYTES + 64 * 1024


# --- OpenAPI ---


def test_openapi_documents_both_contracts_and_every_error_code(service) -> None:
    schema = build_app(service).openapi()
    operation = schema["paths"]["/api/v1/inference/jobs"]["post"]
    body = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    variants = body["properties"]["metadata"]["contentSchema"]["oneOf"]
    assert {tuple(v["required"]) for v in variants} == {
        ("captured_at", "latitude", "longitude"),
        ("camera_utc_offset_minutes",),
    }
    text = json.dumps(operation)
    for code in (
        "FILE_TOO_LARGE", "UNSUPPORTED_MEDIA_FORMAT", "INVALID_VIDEO", "MISSING_GPS_TRACK",
        "INVALID_METADATA", "INVALID_VIDEO_DIMENSIONS", "INVALID_VIDEO_FRAME_RATE",
        "INVALID_VIDEO_DURATION",
    ):
        assert code in text, code
    for status_code in ("202", "413", "415", "422", "503"):
        assert status_code in operation["responses"]
