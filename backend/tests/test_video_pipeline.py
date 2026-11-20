"""Real MP4 decoding, bounded uploads, transactional results, and cleanup."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa

from app.api.upload_limit import UploadLimitMiddleware
from app.api.v1.router import api_router
from app.models import InputMedia, EvidenceMedia, InferenceJob, DetectionRecord
from app.services.inference_jobs import DatabaseInfraScanService
from app.services.inference_processing import InferenceJobProcessor
from app.services.video import inspect_video, sampled_frames, InvalidVideo
from scripts.backfill_r2 import backfill
from test_inference_workflow import InMemoryMediaStorage, RecordingQueue, VALID_METADATA, v0_output
from test_inference_submission import FakeSubmissionService


class StreamStorage(InMemoryMediaStorage):
    def put_file(self, source, key, content_type):
        assert not isinstance(source, bytes)
        self.put(source.read(), key, content_type)

    def download_to(self, key, target, max_bytes):
        data = self.get(key)
        assert len(data) <= max_bytes
        target.write(data)


@pytest.fixture(scope="module")
def mp4(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools required")
    path = tmp_path_factory.mktemp("video") / "road.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=160x120:rate=30", "-t", "3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def client_for(service, limit=None):
    app = FastAPI()
    app.state.infrascan_service = service
    app.include_router(api_router, prefix="/api/v1")
    if limit:
        app.add_middleware(UploadLimitMiddleware, max_bytes=limit)
    return TestClient(app)


def submit(client, payload, content_type="video/mp4"):
    return client.post("/api/v1/inference/jobs", files={
        "file": ("road.mp4", payload, content_type),
        "metadata": (None, json.dumps(VALID_METADATA)),
    })


def test_real_video_frames_are_bounded_and_timestamped(mp4):
    info = inspect_video(mp4)
    assert info.duration == 3 and info.fps == 30
    frames = list(sampled_frames(mp4))
    assert len(frames) == 60
    times = [t for t, _ in frames]
    assert times == sorted(set(times))
    assert times[0] == 0 and times[-1] > 2.9
    assert all(jpeg.startswith(b"\xff\xd8") for _, jpeg in frames)


def test_validated_video_type_comes_from_bytes(mp4):
    service = FakeSubmissionService()
    response = submit(client_for(service), mp4.read_bytes(), "application/octet-stream")
    assert response.status_code == 202, response.text
    assert service.calls[0]["content"] == mp4.read_bytes()


def test_corrupt_mp4_rejected_before_submission():
    service = FakeSubmissionService()
    response = submit(client_for(service), b"\x00\x00\x00\x20ftypisom" + b"bad" * 20)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_VIDEO"
    assert not service.calls


def test_video_size_limit_before_probe(mp4, monkeypatch):
    monkeypatch.setattr("app.api.v1.routes.inference.MAX_FILE_BYTES", 20)
    response = submit(client_for(FakeSubmissionService()), mp4.read_bytes())
    assert response.status_code == 413


def test_request_limit_with_content_length(mp4):
    response = submit(client_for(FakeSubmissionService(), 128), mp4.read_bytes())
    assert response.status_code == 413


def test_request_limit_without_content_length():
    body = b'--b\r\nContent-Disposition: form-data; name="file"; filename="x.mp4"\r\n\r\n' + b'x' * 200 + b'\r\n--b--\r\n'
    response = client_for(FakeSubmissionService(), 128).post(
        "/api/v1/inference/jobs", content=iter([body[:100], body[100:]]),
        headers={"content-type": "multipart/form-data; boundary=b"})
    assert response.status_code == 413, response.text


def test_video_completion_and_frame_evidence(session_factory, mp4, v0_output):
    storage = StreamStorage()
    queue = RecordingQueue(session_factory)
    response = submit(client_for(DatabaseInfraScanService(session_factory, queue, storage)), mp4.read_bytes())
    assert response.status_code == 202, response.text
    job_id = UUID(response.json()["job_id"])
    processor = InferenceJobProcessor(session_factory, lambda *_: (v0_output, b"jpeg"), media_storage=storage)
    assert processor.process(job_id) == "completed"
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert job.input_media.data is None
        assert job.input_media.content_type == "video/mp4"
        evidence = session.scalars(sa.select(EvidenceMedia).where(EvidenceMedia.job_id == job_id)).all()
        assert len(evidence) == 60
        assert all(e.data is None and e.object_storage_key in storage.objects for e in evidence)
        detections = session.scalars(sa.select(DetectionRecord).where(DetectionRecord.job_id == job_id)).all()
        assert len(detections) == 60 * len(v0_output["detections"])
        assert all(d.frame_timestamp == d.evidence.frame_timestamp and d.location is None for d in detections)
    assert queue.committed_at_publish == [True]


@pytest.mark.parametrize("failure", ["missing", "inference", "upload", "database"])
def test_video_failure_cleans_partial_evidence(session_factory, mp4, v0_output, monkeypatch, failure):
    storage = StreamStorage()
    service = DatabaseInfraScanService(session_factory, RecordingQueue(session_factory), storage)
    response = submit(client_for(service), mp4.read_bytes())
    job_id = UUID(response.json()["job_id"])
    if failure == "missing":
        storage.objects.clear()
    calls = 0
    def runner(*_):
        nonlocal calls
        calls += 1
        if failure == "inference" and calls == 2:
            raise RuntimeError("frame failed")
        if failure == "upload" and calls == 2:
            storage._put_error = RuntimeError("storage failed")
        return v0_output, b"jpeg"
    if failure == "database":
        def fail(*args, **kwargs):
            raise RuntimeError("commit path failed")
        monkeypatch.setattr("app.services.inference_processing.DetectionRecordRepository.add_many", fail)
    processor = InferenceJobProcessor(session_factory, runner, media_storage=storage)
    assert processor.process(job_id) == "failed"
    with session_factory() as session:
        assert session.get(InferenceJob, job_id).status == "failed"
        assert session.scalar(sa.select(sa.func.count()).select_from(EvidenceMedia)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(DetectionRecord)) == 0
    assert not any(k.startswith("videos/evidence/") for k in storage.objects)


def test_backfill_is_verified_and_repeatable(session_factory):
    with session_factory() as session, session.begin():
        row = InputMedia(data=b"legacy", content_type="image/jpeg")
        session.add(row)
        session.flush()
        media_id = row.media_id
    storage = StreamStorage()
    assert backfill(session_factory, storage)["input_media"] == 1
    assert backfill(session_factory, storage)["input_media"] == 0
    with session_factory() as session:
        row = session.get(InputMedia, media_id)
        assert row.data == b"legacy"
        assert storage.get(row.object_storage_key) == row.data

@pytest.mark.parametrize("source,codec", [
    ("color=size=160x120:rate=60:duration=0.2", "libx264"),
    ("color=size=1922x1082:rate=1:duration=1", "libx264"),
    ("color=size=16x16:rate=1:duration=601", "libx264"),
    ("color=size=160x120:rate=1:duration=1", "mpeg4"),
])
def test_video_contract_rejects_unsupported_streams(tmp_path, source, codec):
    path = tmp_path / "invalid.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", source,
                    "-c:v", codec, "-pix_fmt", "yuv420p", str(path)], check=True)
    with pytest.raises(InvalidVideo):
        inspect_video(path)
