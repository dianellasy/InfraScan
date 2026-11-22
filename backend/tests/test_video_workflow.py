"""Database-backed MP4 workflow: object storage, worker, evidence, migrations.

Object storage is an in-memory fake (never real R2). Clips are synthetic
`synthetic-a119-mini2-like` seeds generated with FFmpeg at test time.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from a119_seed_support import requires_ffmpeg
from app.adapters.r2_storage import StoredObjectTooLarge
from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.models import (
    DetectionRecord,
    EvidenceMedia,
    InferenceJob,
    InferenceJobStatus,
    InputMedia,
    Issue,
)
from app.services.inference_jobs import DatabaseInfraScanService
from app.services.inference_processing import InferenceJobProcessor
from auth_support import citizen_a, developer
from postgres_support import run_alembic
from test_inference_workflow import (
    FIXTURE_PATH,
    RecordingQueue,
    StepClock,
    _on_event_loop,
    count_rows,
)

OFFSET = {"camera_utc_offset_minutes": -480}
ANNOTATED_JPEG = b"\xff\xd8\xff\xe0annotated-frame\xff\xd9"


class StreamingMediaStorage:
    """In-memory `MediaStorageAdapter` that records how each object arrived."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.read_sizes: list[int] = []
        self.deletes: list[str] = []
        self.fail_put_file: Exception | None = None
        self.fail_put_after: int | None = None
        self.fail_get: Exception | None = None

    def put(self, data: bytes, object_key: str, content_type: str) -> None:
        self.calls.append(("put", object_key))
        if self.fail_put_after is not None:
            if self.fail_put_after == 0:
                raise ConnectionError("storage down")
            self.fail_put_after -= 1
        self.objects[object_key] = (data, content_type)

    def put_file(self, source: BinaryIO, object_key: str, content_type: str) -> None:
        self.calls.append(("put_file", object_key))
        self.put_file_on_event_loop = _on_event_loop()
        chunks = []
        while chunk := source.read(256 * 1024):
            self.read_sizes.append(len(chunk))
            chunks.append(chunk)
            if self.fail_put_file is not None:
                self.objects[object_key] = (b"partial", content_type)
                raise self.fail_put_file
        self.objects[object_key] = (b"".join(chunks), content_type)

    def get(self, key: str) -> bytes:
        self.calls.append(("get", key))
        if self.fail_get is not None:
            raise self.fail_get
        return self.objects[key][0]

    def download_to_file(self, key: str, target: BinaryIO, max_bytes: int) -> int:
        self.calls.append(("download_to_file", key))
        data = self.objects[key][0]
        if len(data) > max_bytes:
            raise StoredObjectTooLarge(key)
        target.write(data)
        return len(data)

    def delete(self, key: str) -> None:
        self.deletes.append(key)
        self.objects.pop(key, None)


class FrameRunner:
    """Fake model: the v0-mvp fixture output for each frame, optionally failing."""

    def __init__(
        self,
        fail_on_call: int | None = None,
        vary_version_on: int | None = None,
        detections: bool = True,
    ):
        self.output = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.output["image_width"], self.output["image_height"] = 1920, 1080
        if not detections:
            self.output["detections"] = []
        self.calls: list[tuple[bytes, str | None]] = []
        self._fail_on_call = fail_on_call
        self._vary_version_on = vary_version_on

    def __call__(self, image: bytes, filename: str | None):
        self.calls.append((image, filename))
        if self._fail_on_call is not None and len(self.calls) == self._fail_on_call:
            raise RuntimeError("model crashed")
        output = dict(self.output)
        if self._vary_version_on is not None and len(self.calls) == self._vary_version_on:
            output["model_version"] = "v1-other"
        return output, ANNOTATED_JPEG


def build_client(
    service: DatabaseInfraScanService, headers: dict[str, str] | None = None
) -> TestClient:
    """A client signed in as citizen A unless other `headers` are given."""
    app = FastAPI()
    app.state.infrascan_service = service
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app, headers=citizen_a() if headers is None else headers)


def submit_clip(client: TestClient, path: Path, metadata: dict = OFFSET):
    return client.post(
        "/api/v1/inference/jobs",
        files=[
            ("file", (path.name, path.read_bytes(), "video/mp4")),
            ("metadata", (None, json.dumps(metadata))),
        ],
    )


def submit_and_process(session_factory, clip: Path, runner=None, storage=None):
    storage = storage or StreamingMediaStorage()
    queue = RecordingQueue(session_factory)
    service = DatabaseInfraScanService(session_factory, queue, storage)
    with build_client(service) as client:
        accepted = submit_clip(client, clip)
        assert accepted.status_code == 202, accepted.json()
    job_id = UUID(accepted.json()["job_id"])
    status = InferenceJobProcessor(
        session_factory, runner or FrameRunner(), StepClock(), media_storage=storage
    ).process(job_id)
    return job_id, status, storage, service


# --- Submission and object storage ---


@requires_ffmpeg
def test_video_submission_streams_to_storage_and_persists_first_fix(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, manifest = a119_seed("cold-start", seconds=6, width=1920, height=1080)
    storage = StreamingMediaStorage()
    queue = RecordingQueue(session_factory)
    with build_client(DatabaseInfraScanService(session_factory, queue, storage)) as client:
        response = submit_clip(client, path)
    assert response.status_code == 202, response.json()
    job_id = UUID(response.json()["job_id"])

    [(method, key)] = storage.calls
    assert method == "put_file"
    assert storage.put_file_on_event_loop is False
    assert max(storage.read_sizes) <= 256 * 1024
    data, content_type = storage.objects[key]
    assert data == path.read_bytes()
    assert content_type == "video/mp4"
    assert queue.job_ids == [job_id] and queue.committed_at_publish == [True]

    expected = manifest["expected_first_fix"]
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        media = job.input_media
        assert key == f"input/{media.media_id}.mp4"
        assert media.data is None
        assert media.object_storage_key == key
        assert media.content_type == "video/mp4"
        assert media.filename == path.name
        assert job.captured_at == datetime.fromisoformat(expected["recorded_at_utc"])
        assert abs(float(job.latitude) - expected["latitude"]) < 1e-6
        assert abs(float(job.longitude) - expected["longitude"]) < 1e-6


@requires_ffmpeg
def test_partial_video_upload_is_deleted_and_nothing_is_persisted(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, _ = a119_seed("standard")
    storage = StreamingMediaStorage()
    storage.fail_put_file = ConnectionError("multipart upload aborted")
    queue = RecordingQueue(session_factory)
    with build_client(DatabaseInfraScanService(session_factory, queue, storage)) as client:
        response = submit_clip(client, path)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SUBMISSION_DEPENDENCY_UNAVAILABLE"
    assert storage.objects == {}
    assert storage.deletes == [storage.calls[0][1]]
    assert queue.job_ids == []
    assert count_rows(session_factory, InputMedia) == 0


@requires_ffmpeg
def test_database_failure_after_upload_deletes_the_object(
    session_factory: sessionmaker[Session], a119_seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.repositories import InferenceJobRepository

    def unavailable(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("database down"))

    monkeypatch.setattr(InferenceJobRepository, "create_pending", unavailable)
    path, _ = a119_seed("standard")
    storage = StreamingMediaStorage()
    queue = RecordingQueue(session_factory)
    with build_client(DatabaseInfraScanService(session_factory, queue, storage)) as client:
        response = submit_clip(client, path)
    assert response.status_code == 503
    assert storage.objects == {}
    assert len(storage.deletes) == 1
    assert queue.job_ids == []
    assert count_rows(session_factory, InputMedia) == 0


def test_submission_without_object_storage_is_unavailable(session_factory) -> None:
    from test_inference_workflow import make_jpeg

    queue = RecordingQueue(session_factory)
    with build_client(DatabaseInfraScanService(session_factory, queue, None)) as client:
        response = client.post(
            "/api/v1/inference/jobs",
            files=[
                ("file", ("road.jpg", make_jpeg(), "image/jpeg")),
                ("metadata", (None, json.dumps({
                    "captured_at": "2026-11-10T14:30:00-08:00",
                    "latitude": 33.8823, "longitude": -117.8851,
                }))),
            ],
        )
    assert response.status_code == 503
    assert queue.job_ids == []


# --- Worker ---


@requires_ffmpeg
def test_worker_samples_frames_with_timestamps_and_per_frame_gps(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, manifest = a119_seed("long-gap", seconds=12, width=1920, height=1080)
    runner = FrameRunner()
    job_id, status, storage, _ = submit_and_process(session_factory, path, runner)
    assert status == InferenceJobStatus.COMPLETED

    assert len(runner.calls) == 60
    assert all(image[:2] == b"\xff\xd8" for image, _ in runner.calls)
    assert runner.calls[0][1] == "20261120123000_000001-00000000.jpg"

    with session_factory() as session:
        evidence = session.scalars(
            sa.select(EvidenceMedia)
            .where(EvidenceMedia.job_id == job_id)
            .order_by(EvidenceMedia.frame_timestamp)
        ).all()
        assert len(evidence) == 60
        timestamps = [row.frame_timestamp for row in evidence]
        assert timestamps[0] == 0.0
        assert timestamps[-1] == pytest.approx(12 - 1 / 30, abs=1e-6)
        for row in evidence:
            assert row.data is None
            ms = round(row.frame_timestamp * 1000)
            assert row.object_storage_key == f"evidence/{job_id}/{ms:08d}-{row.media_id}.jpg"
            assert storage.objects[row.object_storage_key] == (ANNOTATED_JPEG, "image/jpeg")
        records = session.scalars(
            sa.select(DetectionRecord).where(DetectionRecord.job_id == job_id)
        ).all()
        assert len(records) == 60 * 5
        by_media = {row.media_id: row.frame_timestamp for row in evidence}
        for record in records:
            assert record.frame_timestamp == by_media[record.evidence_media]
        tunnel = [r for r in records if 4.2 <= r.frame_timestamp <= 9.8]
        driving = [r for r in records if r.frame_timestamp <= 2.0]
        assert tunnel and all(r.location is None for r in tunnel)
        assert any(r.location is not None for r in driving)
        # Detections are projected ahead of the per-frame GPS point.
        first = manifest["expected_first_fix"]
        assert all(
            r.location is None
            or (r.location["latitude"], r.location["longitude"]) != (first["latitude"], first["longitude"])
            for r in records
        )
    assert storage.calls[1] == ("download_to_file", storage.calls[0][1])


@requires_ffmpeg
def test_detections_api_orders_by_frame_timestamp(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, _ = a119_seed("standard", seconds=4, width=1920, height=1080)
    job_id, status, _, service = submit_and_process(session_factory, path)
    assert status == InferenceJobStatus.COMPLETED
    with build_client(service) as client:
        page = client.get(f"/api/v1/jobs/{job_id}/detections", params={"page_size": 100}).json()
    stamps = [item["frame_timestamp"] for item in page["items"]]
    assert page["total"] == 60 * 5
    assert stamps == sorted(stamps)
    assert stamps[0] == 0.0


def _job_issues(client: TestClient, job_id: UUID | str) -> dict[str, dict]:
    page = client.get("/api/v1/issues", params={"job_id": str(job_id), "page_size": 100}).json()
    assert page["total"] == len(page["items"])
    return {item["issue_id"]: item for item in page["items"]}


def _all_job_detections(client: TestClient, job_id: UUID | str) -> list[dict]:
    items, page = [], 1
    while True:
        body = client.get(
            f"/api/v1/jobs/{job_id}/detections", params={"page": page, "page_size": 100}
        ).json()
        items += body["items"]
        if len(items) >= body["total"]:
            return items
        page += 1


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@requires_ffmpeg
def test_stationary_clip_links_repeated_frames_and_repeat_submissions(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    """A parked camera sees the same five boxes in all 60 sampled frames.

    With the provisional A119 profile only the 0.635 "critical" box projects
    (about 11 m ahead); the other four stay unlocated. Every located frame
    therefore projects to one identical point.
    """
    path, manifest = a119_seed("stationary", seconds=4, width=1920, height=1080)
    storage = StreamingMediaStorage()
    first_job, status, _, service = submit_and_process(session_factory, path, storage=storage)
    assert status == InferenceJobStatus.COMPLETED
    second_job, status, _, _ = submit_and_process(session_factory, path, storage=storage)
    assert status == InferenceJobStatus.COMPLETED

    captured_at = datetime.fromisoformat(manifest["expected_first_fix"]["recorded_at_utc"])
    fix = manifest["expected_first_fix"]
    with build_client(service) as client:
        first_items = _all_job_detections(client, first_job)
        second_items = _all_job_detections(client, second_job)
        first_issues = _job_issues(client, first_job)
        second_issues = _job_issues(client, second_job)
        viewport = {
            "min_latitude": fix["latitude"] - 0.001, "max_latitude": fix["latitude"] + 0.001,
            "min_longitude": fix["longitude"] - 0.001, "max_longitude": fix["longitude"] + 0.001,
        }
        issue_map = client.get("/api/v1/issues/map", params=viewport).json()
        queue = client.get("/api/v1/issues/review-queue", params={"page_size": 100}).json()
        located_id = next(i for i in first_issues if first_issues[i]["latitude"] is not None)
        located_detections = client.get(
            f"/api/v1/issues/{located_id}/detections", params={"page_size": 100}
        ).json()
        thumbnail = client.get(first_issues[located_id]["evidence_thumbnail_url"])

    assert len(first_items) == len(second_items) == 60 * 5
    timestamps = sorted({item["frame_timestamp"] for item in first_items})
    assert len(timestamps) == 60
    for item in first_items + second_items:
        # Frame UTC = first fix time + offset from the first fix (slot 0 here).
        expected = captured_at + timedelta(seconds=item["frame_timestamp"] - fix["slot"])
        assert abs(_utc(item["observed_at"]) - expected) <= timedelta(microseconds=1)

    # One located Issue shared by both submissions; four unlocated Issues per job.
    assert len(first_issues) == len(second_issues) == 5
    assert set(first_issues) & set(second_issues) == {located_id}
    assert count_rows(session_factory, Issue) == 9

    located = first_issues[located_id]
    located_items = [i for i in first_items + second_items if i["location"] is not None]
    assert len(located_items) == 120
    assert {i["issue_id"] for i in located_items} == {located_id}
    assert {(i["latitude"], i["longitude"]) for i in located_items} == {
        (located["latitude"], located["longitude"])
    }
    assert located["detection_count"] == located_detections["total"] == 120
    assert (located["severity"], located["status"], located["review_reason"]) == (
        "critical", "pending_review", None,
    )
    assert located["location_accuracy_m"] is None
    assert _utc(located["first_seen_at"]) == captured_at
    assert abs(_utc(located["last_seen_at"]) - (captured_at + timedelta(seconds=timestamps[-1]))) <= timedelta(microseconds=1)
    assert thumbnail.status_code == 200 and thumbnail.content == ANNOTATED_JPEG

    for job_items, job_issues in ((first_items, first_issues), (second_items, second_issues)):
        per_issue = Counter(item["issue_id"] for item in job_items)
        frames_per_issue = defaultdict(set)
        for item in job_items:
            frames_per_issue[item["issue_id"]].add(item["evidence_media"])
        # One detection per sampled frame per Issue: 60 each.
        assert set(per_issue.values()) == {60}
        assert all(len(frames) == 60 for frames in frames_per_issue.values())
        for issue_id, issue in job_issues.items():
            if issue_id == located_id:
                continue
            assert (issue["latitude"], issue["review_reason"]) == (None, "missing_location")
            assert issue["detection_count"] == 60

    [feature] = issue_map["features"]
    assert feature["id"] == located_id
    assert feature["geometry"]["coordinates"] == [located["longitude"], located["latitude"]]
    assert {item["issue_id"] for item in queue["items"]} == set(first_issues) | set(second_issues)


@requires_ffmpeg
def test_zero_detection_video_completes_with_evidence_per_frame(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, _ = a119_seed("standard")
    job_id, status, storage, _ = submit_and_process(
        session_factory, path, FrameRunner(detections=False)
    )
    assert status == InferenceJobStatus.COMPLETED
    assert count_rows(session_factory, DetectionRecord) == 0
    assert count_rows(session_factory, EvidenceMedia) == 60
    assert count_rows(session_factory, Issue) == 0
    with session_factory() as session:
        assert session.get(InferenceJob, job_id).status == "completed"


@requires_ffmpeg
def test_legacy_inline_video_row_still_processes(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, manifest = a119_seed("standard")
    first = manifest["expected_first_fix"]
    with session_factory() as session, session.begin():
        job = InferenceJob(
            input_media=InputMedia(
                data=path.read_bytes(), content_type="video/mp4", filename=path.name
            ),
            captured_at=datetime.fromisoformat(first["recorded_at_utc"]),
            latitude=Decimal(repr(first["latitude"])),
            longitude=Decimal(repr(first["longitude"])),
        )
        session.add(job)
        session.flush()
        job_id = job.job_id
    storage = StreamingMediaStorage()
    status = InferenceJobProcessor(
        session_factory, FrameRunner(), StepClock(), media_storage=storage
    ).process(job_id)
    assert status == InferenceJobStatus.COMPLETED
    assert not any(method == "download_to_file" for method, _ in storage.calls)


@requires_ffmpeg
@pytest.mark.parametrize(
    "failure", ["model", "model_version", "evidence_upload", "commit", "issue_link"]
)
def test_video_failure_cleans_up_uploaded_evidence(
    session_factory: sessionmaker[Session],
    a119_seed,
    failure: str,
    caplog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.repositories import DetectionRecordRepository

    path, _ = a119_seed("standard")
    storage = StreamingMediaStorage()
    runner = FrameRunner(
        fail_on_call=5 if failure == "model" else None,
        vary_version_on=5 if failure == "model_version" else None,
    )
    if failure == "evidence_upload":
        storage.fail_put_after = 4
    if failure == "commit":
        calls = {"count": 0}
        original = DetectionRecordRepository.add_many

        def fail_late(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 30:
                raise RuntimeError("commit failed")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(DetectionRecordRepository, "add_many", fail_late)
    if failure == "issue_link":
        from app.services.issue_linking import IssueLinker

        original_attach = IssueLinker._attach
        attached = {"count": 0}

        def fail_mid_link(self, *args, **kwargs):
            attached["count"] += 1
            if attached["count"] == 150:
                raise RuntimeError("issue link failed")
            return original_attach(self, *args, **kwargs)

        monkeypatch.setattr(IssueLinker, "_attach", fail_mid_link)
    caplog.set_level(logging.INFO)
    job_id, status, storage, _ = submit_and_process(session_factory, path, runner, storage)
    assert status == InferenceJobStatus.FAILED

    evidence_puts = [key for method, key in storage.calls if method == "put"]
    # Frames before the failure were uploaded; a failed upload attempt is cleaned up too.
    expected_puts = {
        "model": 4, "model_version": 4, "evidence_upload": 5, "commit": 60, "issue_link": 60,
    }
    assert len(evidence_puts) == expected_puts[failure]
    assert set(evidence_puts) <= set(storage.deletes)
    assert [k for k in storage.objects if k.startswith("evidence/")] == []
    with session_factory() as session:
        assert session.get(InferenceJob, job_id).status == "failed"
    assert count_rows(session_factory, EvidenceMedia) == 0
    assert count_rows(session_factory, DetectionRecord) == 0
    assert count_rows(session_factory, Issue) == 0


@requires_ffmpeg
def test_worker_rejects_stored_video_whose_gps_differs_from_the_job(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, _ = a119_seed("standard")
    storage = StreamingMediaStorage()
    queue = RecordingQueue(session_factory)
    service = DatabaseInfraScanService(session_factory, queue, storage)
    with build_client(service) as client:
        job_id = UUID(submit_clip(client, path).json()["job_id"])
    other, _ = a119_seed("southeast")
    key = next(iter(storage.objects))
    storage.objects[key] = (other.read_bytes(), "video/mp4")

    runner = FrameRunner()
    status = InferenceJobProcessor(
        session_factory, runner, StepClock(), media_storage=storage
    ).process(job_id)
    assert status == InferenceJobStatus.FAILED
    assert runner.calls == []


@requires_ffmpeg
def test_worker_revalidates_the_stored_object(
    session_factory: sessionmaker[Session], a119_seed
) -> None:
    path, _ = a119_seed("standard")
    storage = StreamingMediaStorage()
    queue = RecordingQueue(session_factory)
    with build_client(DatabaseInfraScanService(session_factory, queue, storage)) as client:
        job_id = UUID(submit_clip(client, path).json()["job_id"])
    key = next(iter(storage.objects))
    storage.objects[key] = (path.read_bytes()[:30_000], "video/mp4")
    status = InferenceJobProcessor(
        session_factory, FrameRunner(), StepClock(), media_storage=storage
    ).process(job_id)
    assert status == InferenceJobStatus.FAILED


# --- Evidence reads ---


def _legacy_job_with_evidence(session_factory, data: bytes | None, key: str | None) -> UUID:
    with session_factory() as session, session.begin():
        job = InferenceJob(
            input_media=InputMedia(data=b"jpeg", content_type="image/jpeg", filename="road.jpg"),
            captured_at=datetime(2026, 11, 10, 22, 30, tzinfo=UTC),
            latitude=Decimal("33.8823"),
            longitude=Decimal("-117.8851"),
        )
        session.add(job)
        session.flush()
        evidence = EvidenceMedia(
            job_id=job.job_id, data=data, content_type="image/jpeg",
            filename="annotated.jpg", object_storage_key=key,
        )
        session.add(evidence)
        session.flush()
        return evidence.media_id


def test_legacy_inline_evidence_is_still_served(session_factory) -> None:
    storage = StreamingMediaStorage()
    media_id = _legacy_job_with_evidence(session_factory, ANNOTATED_JPEG, None)
    service = DatabaseInfraScanService(session_factory, RecordingQueue(session_factory), storage)
    with build_client(service, developer()) as client:
        response = client.get(f"/api/v1/evidence/{media_id}")
    assert response.status_code == 200
    assert response.content == ANNOTATED_JPEG
    assert storage.calls == []


def test_object_storage_evidence_is_served_and_unavailable_maps_to_503(session_factory) -> None:
    storage = StreamingMediaStorage()
    key = f"evidence/{uuid4()}/{uuid4()}.jpg"
    storage.objects[key] = (ANNOTATED_JPEG, "image/jpeg")
    media_id = _legacy_job_with_evidence(session_factory, None, key)
    service = DatabaseInfraScanService(session_factory, RecordingQueue(session_factory), storage)
    with build_client(service, developer()) as client:
        assert client.get(f"/api/v1/evidence/{media_id}").content == ANNOTATED_JPEG
        storage.fail_get = ConnectionError("r2 down")
        response = client.get(f"/api/v1/evidence/{media_id}")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EVIDENCE_UNAVAILABLE"
    assert "r2 down" not in response.text


def test_media_rows_need_inline_data_or_an_object_key(session_factory) -> None:
    with pytest.raises(IntegrityError):
        with session_factory() as session, session.begin():
            session.add(InputMedia(data=None, content_type="video/mp4", filename="x.MP4"))


# --- End to end through the Celery task ---


@requires_ffmpeg
def test_end_to_end_http_r2_celery_database_api(
    session_factory: sessionmaker[Session], a119_seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from inference.celery import tasks

    path, manifest = a119_seed("cold-start", seconds=6, width=1920, height=1080, audio=True)
    storage = StreamingMediaStorage()
    runner = FrameRunner()
    queue = RecordingQueue(session_factory)
    service = DatabaseInfraScanService(session_factory, queue, storage)
    monkeypatch.setattr(tasks, "R2MediaStorageAdapter", lambda: storage)
    monkeypatch.setattr(tasks.database, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(tasks, "run_v0_mvp_inference", runner)

    with build_client(service) as client:
        accepted = submit_clip(client, path)
        assert accepted.status_code == 202, accepted.json()
        job_id = accepted.json()["job_id"]
        assert queue.job_ids == [UUID(job_id)]

        tasks.process_inference_job(job_id)

        job = client.get(f"/api/v1/jobs/{job_id}").json()
        pages = [
            client.get(
                f"/api/v1/jobs/{job_id}/detections", params={"page": page, "page_size": 100}
            ).json()
            for page in (1, 2, 3)
        ]
        items = [item for page in pages for item in page["items"]]
        first_item = items[0]
        evidence = client.get(f"/api/v1/evidence/{first_item['evidence_media']}")
        job_issues = _job_issues(client, job_id)
        fix = manifest["expected_first_fix"]
        viewport = {
            "min_latitude": fix["latitude"] - 0.01, "max_latitude": fix["latitude"] + 0.01,
            "min_longitude": fix["longitude"] - 0.01, "max_longitude": fix["longitude"] + 0.01,
        }
        issue_map = client.get("/api/v1/issues/map", params=viewport).json()
        queue = client.get("/api/v1/issues/review-queue", params={"page_size": 100}).json()
        issue_detections = {
            issue_id: client.get(
                f"/api/v1/issues/{issue_id}/detections", params={"page_size": 100}
            ).json()
            for issue_id in job_issues
        }
        thumbnails = {
            issue_id: client.get(issue["evidence_thumbnail_url"]).status_code
            for issue_id, issue in job_issues.items()
        }

    expected = manifest["expected_first_fix"]
    assert job["status"] == "completed"
    assert job["model_version"] == "v0-mvp"
    assert job["metadata"]["captured_at"] == expected["recorded_at_utc"]
    assert job["metadata"]["latitude"] == pytest.approx(expected["latitude"], abs=1e-6)
    assert job["metadata"]["longitude"] == pytest.approx(expected["longitude"], abs=1e-6)
    assert len(runner.calls) == 60
    assert pages[0]["total"] == len(items) == 60 * 5
    assert first_item["frame_timestamp"] == 0.0
    # Pre-lock frames (first fix at 3 s, 1 s nearest-fix tolerance) stay unlocated.
    assert first_item["location"] is None
    located = [d for d in items if d["location"] is not None]
    assert located and all(d["frame_timestamp"] >= 2.0 for d in located)
    # Boxes beyond the ground-projection range stay unlocated, but every frame
    # after the first fix has a GPS base point, so some of its detections project.
    frames_after_fix = {d["evidence_media"] for d in items if d["frame_timestamp"] >= 3.0}
    assert frames_after_fix == {d["evidence_media"] for d in located if d["frame_timestamp"] >= 3.0}
    assert evidence.status_code == 200 and evidence.content == ANNOTATED_JPEG
    input_keys = [k for k in storage.objects if k.startswith("input/")]
    assert input_keys and input_keys[0].endswith(".mp4")

    # Frame times follow the GPS timeline: the first fix is 3 s into the clip,
    # so earlier frames are extrapolated back from it (not the worker clock).
    first_fix_at = _utc(expected["recorded_at_utc"])
    for item in items:
        offset = timedelta(seconds=item["frame_timestamp"] - expected["slot"])
        assert abs(_utc(item["observed_at"]) - (first_fix_at + offset)) <= timedelta(microseconds=1)
    assert _utc(first_item["observed_at"]) == first_fix_at - timedelta(seconds=3)

    # Every detection supports exactly one Issue, with at most one detection per frame.
    assert all(item["issue_id"] in job_issues for item in items)
    by_issue = defaultdict(list)
    for item in items:
        by_issue[item["issue_id"]].append(item)
    assert set(by_issue) == set(job_issues)
    for issue_id, supporting in by_issue.items():
        issue = job_issues[issue_id]
        assert issue["detection_count"] == issue_detections[issue_id]["total"] == len(supporting)
        assert {d["detection_id"] for d in issue_detections[issue_id]["items"]} == {
            d["detection_id"] for d in supporting
        }
        assert len({d["evidence_media"] for d in supporting}) == len(supporting)
        observed = sorted(_utc(d["observed_at"]) for d in supporting)
        assert (_utc(issue["first_seen_at"]), _utc(issue["last_seen_at"])) == (observed[0], observed[-1])
        assert issue["status"] == "pending_review"
        assert thumbnails[issue_id] == 200

    # Unlocated detections: the four boxes the provisional profile never
    # projects, each tracked through all 60 frames, plus the projectable box
    # in the frames before GPS lock. They are review-only Issues.
    unlocated = {i: issue for i, issue in job_issues.items() if issue["latitude"] is None}
    prelock = [d for d in located_box(items) if d["location"] is None]
    assert sorted(len(by_issue[i]) for i in unlocated) == sorted([len(prelock)] + [60] * 4)
    assert len({d["issue_id"] for d in prelock}) == 1
    assert all(issue["review_reason"] == "missing_location" for issue in unlocated.values())
    assert set(unlocated) <= {item["issue_id"] for item in queue["items"]}

    # Located detections chain into Issues as the car moves; each Issue's point
    # is the mean of its detections and is its map marker ([longitude, latitude]).
    located_ids = set(job_issues) - set(unlocated)
    assert located_ids and max(len(by_issue[i]) for i in located_ids) > 1
    assert sum(len(by_issue[i]) for i in located_ids) == len(located)
    for issue_id in located_ids:
        supporting = by_issue[issue_id]
        issue = job_issues[issue_id]
        assert issue["latitude"] == pytest.approx(
            sum(d["latitude"] for d in supporting) / len(supporting), abs=1e-9
        )
        assert issue["longitude"] == pytest.approx(
            sum(d["longitude"] for d in supporting) / len(supporting), abs=1e-9
        )
        assert issue["severity"] == "critical" and issue["review_reason"] is None
    features = {feature["id"]: feature for feature in issue_map["features"]}
    assert set(features) == located_ids
    for issue_id in located_ids:
        assert features[issue_id]["geometry"]["coordinates"] == [
            job_issues[issue_id]["longitude"], job_issues[issue_id]["latitude"],
        ]


def located_box(items: list[dict]) -> list[dict]:
    """Detections of the one fixture box the provisional A119 profile can project."""
    box = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["detections"][1]["bbox_xyxy"]
    return [item for item in items if item["bounding_box"] == box]


# --- Migrations ---


def test_storage_migrations_preserve_legacy_rows_and_refuse_lossy_downgrade(
    disposable_postgres_url: str,
) -> None:
    url = disposable_postgres_url
    assert run_alembic("upgrade", "4b7d2e9c5a18", database_url=url).returncode == 0
    from app.db import create_db_engine

    engine = create_db_engine(url)
    try:
        with engine.begin() as conn:
            media_id = conn.execute(sa.text(
                "INSERT INTO input_media (data, content_type, filename) "
                "VALUES ('\\xffd8'::bytea, 'image/jpeg', 'legacy.jpg') RETURNING media_id"
            )).scalar_one()
            job_id = conn.execute(sa.text(
                "INSERT INTO inference_jobs (input_media_id, captured_at, latitude, longitude) "
                "VALUES (:m, now(), 33.8, -117.9) RETURNING job_id"
            ), {"m": media_id}).scalar_one()
            conn.execute(sa.text(
                "INSERT INTO evidence_media (job_id, data, content_type, filename) "
                "VALUES (:j, '\\xffd9'::bytea, 'image/jpeg', 'legacy-evidence.jpg')"
            ), {"j": job_id})

        up = run_alembic("upgrade", "head", database_url=url)
        assert up.returncode == 0, up.stderr
        with engine.begin() as conn:
            row = conn.execute(sa.text(
                "SELECT data, object_storage_key FROM input_media WHERE media_id = :m"
            ), {"m": media_id}).one()
            assert (bytes(row.data), row.object_storage_key) == (b"\xff\xd8", None)
            evidence = conn.execute(sa.text(
                "SELECT data, frame_timestamp FROM evidence_media WHERE job_id = :j"
            ), {"j": job_id}).one()
            assert (bytes(evidence.data), evidence.frame_timestamp) == (b"\xff\xd9", None)
            r2_only = conn.execute(sa.text(
                "INSERT INTO input_media (data, content_type, filename, object_storage_key) "
                "VALUES (NULL, 'video/mp4', 'clip.MP4', 'input/x.mp4') RETURNING media_id"
            )).scalar_one()
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(sa.text(
                    "UPDATE evidence_media SET frame_timestamp = -1 WHERE job_id = :j"
                ), {"j": job_id})

        refused = run_alembic("downgrade", "4b7d2e9c5a18", database_url=url)
        assert refused.returncode != 0
        assert "R2-only media rows" in refused.stderr

        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM input_media WHERE media_id = :m"), {"m": r2_only})
        down = run_alembic("downgrade", "4b7d2e9c5a18", database_url=url)
        assert down.returncode == 0, down.stderr
        with engine.begin() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM evidence_media")).scalar_one() == 1
    finally:
        engine.dispose()
