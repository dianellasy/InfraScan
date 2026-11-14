"""London Bridge POC geolocation through submission, worker, persistence, and API.

The model is mocked with the recorded multi.png fixture (see
`test_inference_workflow.py`); `test_real_model_pipeline.py` covers the real
model, including the seed command.
"""

from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from app import seed_poc
from app.geolocation import LONDON_BRIDGE_POC, PROJECTION_STATUSES
from app.geolocation.projection import project_ground_to_pixel
from app.models import DetectionRecord, EvidenceMedia, InferenceJob, InputMedia
from app.services.inference_jobs import DatabaseInfraScanService
from app.services.inference_processing import InferenceJobProcessor
from test_inference_workflow import (
    FIXTURE_PATH,
    RecordingQueue,
    build_client,
    count_rows,
    make_jpeg,
)

LONDON = (LONDON_BRIDGE_POC.source.latitude, LONDON_BRIDGE_POC.source.longitude)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def annotated_jpeg(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color=color).save(buffer, format="JPEG")
    return buffer.getvalue()


def poc_service(session_factory: sessionmaker[Session]) -> tuple[DatabaseInfraScanService, RecordingQueue]:
    queue = RecordingQueue(session_factory)
    return DatabaseInfraScanService(session_factory, queue, poc_geolocation=LONDON_BRIDGE_POC), queue


def submit(client: TestClient, latitude: float, longitude: float, image: bytes | None = None):
    metadata = {"captured_at": "2026-11-10T14:30:00-08:00", "latitude": latitude, "longitude": longitude}
    return client.post(
        "/api/v1/inference/jobs",
        files=[
            ("file", ("road.jpg", image or make_jpeg(), "image/jpeg")),
            ("metadata", (None, json.dumps(metadata))),
        ],
    )


def runner_for(output: dict, annotated: bytes):
    return lambda image, filename: (copy.deepcopy(output), annotated)


def to_local_meters(lat: float, lon: float) -> tuple[float, float]:
    """Inverse of `offset_to_wgs84` around London Bridge (ellipsoid radii)."""
    import math

    a, e2 = 6_378_137.0, 0.0066943799901413165
    phi = math.radians(LONDON[0])
    w = 1 - e2 * math.sin(phi) ** 2
    meridional = a * (1 - e2) / w**1.5
    prime = a / math.sqrt(w)
    north = math.radians(lat - LONDON[0]) * meridional
    east = math.radians(lon - LONDON[1]) * prime * math.cos(phi)
    return east, north


# --- Submission ---


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(33.8823, -117.8851), (-33.8688, 151.2093), (0.0, 0.0), (51.507921, -0.0877)],
)
def test_poc_submission_persists_london_bridge_source_regardless_of_request_gps(
    session_factory: sessionmaker[Session], latitude: float, longitude: float
) -> None:
    service, queue = poc_service(session_factory)
    with build_client(service) as client:
        response = submit(client, latitude, longitude)
        assert response.status_code == 202
        assert set(response.json()) == {"job_id", "status", "submitted_at"}
        job_id = UUID(response.json()["job_id"])
        body = client.get(f"/api/v1/jobs/{job_id}").json()

    assert queue.job_ids == [job_id]
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert (float(job.latitude), float(job.longitude)) == LONDON
        provenance = job.geolocation
    assert provenance["mode"] == "poc_assumed"
    assert provenance["assumed"] is True
    assert provenance["request_gps"] == {"latitude": latitude, "longitude": longitude, "used": False}
    assert provenance["source"]["name"] == "London Bridge, London, UK"
    assert provenance["camera"] == LONDON_BRIDGE_POC.camera.to_dict()
    assert provenance["camera"]["profile"]["calibrated"] is False
    assert provenance["intrinsics"] is None  # filled in by the worker

    assert (body["metadata"]["latitude"], body["metadata"]["longitude"]) == LONDON
    assert body["geolocation"]["mode"] == "poc_assumed"
    assert body["geolocation"]["source"]["latitude"] == LONDON[0]
    assert body["status"] == "pending"
    assert body["annotated_image_url"] is None


def test_without_poc_mode_request_gps_is_kept(session_factory: sessionmaker[Session]) -> None:
    service = DatabaseInfraScanService(session_factory, RecordingQueue(session_factory))
    with build_client(service) as client:
        job_id = submit(client, 33.8823, -117.8851).json()["job_id"]
        body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert (body["metadata"]["latitude"], body["metadata"]["longitude"]) == (33.8823, -117.8851)
    assert body["geolocation"] is None


def test_poc_submission_keeps_existing_validation(session_factory: sessionmaker[Session]) -> None:
    service, queue = poc_service(session_factory)
    with build_client(service) as client:
        response = submit(client, 91.0, 0.0)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_COORDINATE"
    assert queue.job_ids == []


# --- Worker geolocation and API ---


def test_worker_persists_detection_locations_and_api_returns_them(
    session_factory: sessionmaker[Session],
) -> None:
    service, queue = poc_service(session_factory)
    evidence = annotated_jpeg((200, 30, 30))
    with build_client(service) as client:
        job_id = UUID(submit(client, 33.8823, -117.8851).json()["job_id"])
        InferenceJobProcessor(session_factory, runner_for(FIXTURE, evidence)).process(queue.job_ids[0])
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        detections = client.get(f"/api/v1/jobs/{job_id}/detections").json()["items"]
        image = client.get(job["annotated_image_url"])

    assert job["status"] == "completed"
    assert job["geolocation"]["image"] == {"width": 886, "height": 595, "exif_orientation_applied": True}
    intrinsics = job["geolocation"]["intrinsics"]
    assert intrinsics["fx"] == pytest.approx(582.349, abs=1e-3)
    assert (intrinsics["cx"], intrinsics["cy"]) == (443.0, 297.5)
    assert job["annotated_media_id"] is not None
    assert image.status_code == 200 and image.content == evidence

    k = LONDON_BRIDGE_POC.camera.profile.intrinsics_for(886, 595)
    assert len(detections) == 5
    for item in detections:
        assert item["location_status"] == "estimated"
        assert item["latitude"] == item["location"]["latitude"]
        assert item["longitude"] == item["location"]["longitude"]
        details = item["location_details"]
        assert details["basis"] == "poc_assumed"
        x1, _, x2, y2 = item["bounding_box"]
        assert details["contact_pixel"] == [(x1 + x2) / 2, y2]
        # Independent check: the stored coordinate projects back to the contact pixel.
        east, north = to_local_meters(item["latitude"], item["longitude"])
        assert (east, north) == pytest.approx((details["east_m"], details["north_m"]), abs=1e-3)
        pixel = project_ground_to_pixel(east, north, k, LONDON_BRIDGE_POC.camera.orientation, 1.5)
        assert pixel == pytest.approx(details["contact_pixel"], abs=0.01)
        assert 0 < details["ground_distance_m"] <= 50
        assert item["evidence_media_url"] == job["annotated_image_url"]

    with session_factory() as session:
        rows = session.scalars(sa.select(DetectionRecord).where(DetectionRecord.job_id == job_id)).all()
        assert {r.location_status for r in rows} == {"estimated"}
        assert all(set(r.location) == {"latitude", "longitude"} for r in rows)


def test_invalid_projections_keep_detection_with_null_location(
    session_factory: sessionmaker[Session],
) -> None:
    output = copy.deepcopy(FIXTURE)
    k = LONDON_BRIDGE_POC.camera.profile.intrinsics_for(886, 595)
    horizon = k.cy - k.fy * 0.36397023426620234  # tan(20 deg)
    template = output["detections"][0]
    output["detections"] = [
        {**template, "bbox_xyxy": [400.0, 10.0, 480.0, 60.0]},  # above the horizon
        {**template, "bbox_xyxy": [400.0, 70.0, 480.0, horizon + 0.5]},  # unstable range
        {**template, "bbox_xyxy": [400.0, 450.0, 480.0, 540.0]},  # valid
    ]
    service, queue = poc_service(session_factory)
    with build_client(service) as client:
        job_id = submit(client, 1.0, 2.0).json()["job_id"]
        InferenceJobProcessor(session_factory, runner_for(output, annotated_jpeg((0, 0, 0)))).process(
            queue.job_ids[0]
        )
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        items = client.get(f"/api/v1/jobs/{job_id}/detections").json()["items"]

    assert job["status"] == "completed"
    by_status = {item["location_status"]: item for item in items}
    assert set(by_status) == {"above_horizon", "unstable_near_horizon", "estimated"}
    for status in ("above_horizon", "unstable_near_horizon"):
        item = by_status[status]
        assert item["location"] is None and item["latitude"] is None and item["longitude"] is None
        assert item["location_details"]["message"]
        assert "east_m" not in item["location_details"] or item["location_details"]["east_m"] is None
    assert by_status["estimated"]["location"] is not None
    assert set(PROJECTION_STATUSES) >= set(by_status)


def test_jobs_without_geolocation_config_are_not_geolocated(
    session_factory: sessionmaker[Session],
) -> None:
    queue = RecordingQueue(session_factory)
    service = DatabaseInfraScanService(session_factory, queue)
    with build_client(service) as client:
        job_id = submit(client, 33.0, -117.0).json()["job_id"]
        InferenceJobProcessor(session_factory, runner_for(FIXTURE, annotated_jpeg((1, 2, 3)))).process(
            queue.job_ids[0]
        )
        items = client.get(f"/api/v1/jobs/{job_id}/detections").json()["items"]
    assert len(items) == 5
    assert all(i["location"] is None and i["location_status"] is None for i in items)


# --- Annotated media ---


def test_annotated_media_belongs_to_the_requested_job(session_factory: sessionmaker[Session]) -> None:
    service, queue = poc_service(session_factory)
    red, blue = annotated_jpeg((220, 0, 0)), annotated_jpeg((0, 0, 220))
    with build_client(service) as client:
        first = submit(client, 1.0, 1.0).json()["job_id"]
        second = submit(client, 2.0, 2.0).json()["job_id"]
        InferenceJobProcessor(session_factory, runner_for(FIXTURE, red)).process(UUID(first))
        InferenceJobProcessor(session_factory, runner_for(FIXTURE, blue)).process(UUID(second))

        jobs = {jid: client.get(f"/api/v1/jobs/{jid}").json() for jid in (first, second)}
        assert jobs[first]["annotated_image_url"] != jobs[second]["annotated_image_url"]
        for jid, expected in ((first, red), (second, blue)):
            url = jobs[jid]["annotated_image_url"]
            assert url.startswith("http://testserver/api/v1/media/evidence/")
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/jpeg"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.content == expected
            for item in client.get(f"/api/v1/jobs/{jid}/detections").json()["items"]:
                assert client.get(item["evidence_media_url"]).content == expected

        missing = client.get(f"/api/v1/media/evidence/{uuid4()}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "MEDIA_NOT_FOUND"
        # An input media ID is not an evidence media ID.
        with session_factory() as session:
            input_id = session.get(InferenceJob, UUID(first)).input_media_id
        assert client.get(f"/api/v1/media/evidence/{input_id}").status_code == 404
    assert len(queue.job_ids) == 2


def test_zero_detection_job_still_exposes_its_annotated_image(
    session_factory: sessionmaker[Session],
) -> None:
    service, queue = poc_service(session_factory)
    evidence = annotated_jpeg((10, 200, 10))
    with build_client(service) as client:
        job_id = submit(client, 1.0, 1.0).json()["job_id"]
        InferenceJobProcessor(
            session_factory, runner_for({**FIXTURE, "detections": []}, evidence)
        ).process(queue.job_ids[0])
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        detections = client.get(f"/api/v1/jobs/{job_id}/detections").json()
        image = client.get(job["annotated_image_url"])

    assert job["status"] == "completed"
    assert detections["total"] == 0
    assert job["geolocation"]["intrinsics"] is not None
    assert image.status_code == 200 and image.content == evidence


def test_detection_evidence_links_original_and_annotated_images(
    session_factory: sessionmaker[Session],
) -> None:
    service, queue = poc_service(session_factory)
    original, evidence = make_jpeg(80, 60), annotated_jpeg((5, 5, 250))
    with build_client(service) as client:
        job_id = submit(client, 1.0, 1.0, image=original).json()["job_id"]
        InferenceJobProcessor(session_factory, runner_for(FIXTURE, evidence)).process(queue.job_ids[0])
        detection = client.get(f"/api/v1/jobs/{job_id}/detections").json()["items"][0]
        body = client.get(f"/api/v1/detections/{detection['detection_id']}/evidence").json()
        frame = body["frames"][0]
        assert body["detection_id"] == detection["detection_id"]
        assert client.get(frame["original_url"]).content == original
        assert client.get(frame["annotated_url"]).content == evidence
        x1, y1, x2, y2 = detection["bounding_box"]
        assert frame["bounding_box"] == {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

        missing = client.get(f"/api/v1/detections/{uuid4()}/evidence")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DETECTION_NOT_FOUND"


# --- Seed command ---


class SeedRunner:
    def __init__(self) -> None:
        self.inputs: list[tuple[bytes, str | None]] = []

    def __call__(self):
        def run(image: bytes, filename: str | None):
            self.inputs.append((image, filename))
            return copy.deepcopy(FIXTURE), annotated_jpeg((90, 90, 90))

        return run


def test_seed_is_repeatable_without_duplicates(
    session_factory: sessionmaker[Session], migrated_engine: sa.Engine
) -> None:
    source_digest = hashlib.sha256(seed_poc.DEFAULT_SOURCE.read_bytes()).hexdigest()
    runner = SeedRunner()
    urls = seed_poc.SeedUrls("http://api.example", "http://ui.example")

    first = seed_poc.seed(migrated_engine, run_inference=runner, urls=urls, log=lambda _: None)
    second = seed_poc.seed(migrated_engine, run_inference=runner, urls=urls, log=lambda _: None)

    assert first.created is True and second.created is False
    assert first.job.job_id == second.job.job_id
    assert second.job.status == "completed"
    assert len(runner.inputs) == 1  # the model ran once, on the submitted bytes
    for model in (InferenceJob, InputMedia, EvidenceMedia):
        assert count_rows(session_factory, model) == 1
    assert count_rows(session_factory, DetectionRecord) == 5

    submitted, filename = runner.inputs[0]
    assert filename == seed_poc.SEED_FILENAME
    assert submitted == seed_poc.jpeg_copy(seed_poc.DEFAULT_SOURCE)
    with Image.open(BytesIO(submitted)) as image:
        assert (image.format, image.size) == ("JPEG", (886, 595))
    assert hashlib.sha256(seed_poc.DEFAULT_SOURCE.read_bytes()).hexdigest() == source_digest

    job = second.job
    assert (job.metadata.latitude, job.metadata.longitude) == LONDON
    assert job.geolocation.mode == "poc_assumed"
    assert job.annotated_image_url == f"http://api.example/api/v1/media/evidence/{job.annotated_media_id}"
    assert all(d.location_status == "estimated" for d in second.detections)
    report = seed_poc._format_report(second, urls)
    assert f"http://ui.example/?job={job.job_id}" in report
    assert "reused, no new records" in report


def test_seed_retries_after_a_failed_seed_job(
    session_factory: sessionmaker[Session], migrated_engine: sa.Engine
) -> None:
    def failing():
        def run(image, filename):
            raise RuntimeError("model crashed")

        return run

    failed = seed_poc.seed(migrated_engine, run_inference=failing, log=lambda _: None)
    assert failed.job.status == "failed"

    retried = seed_poc.seed(migrated_engine, run_inference=SeedRunner(), log=lambda _: None)
    assert retried.created is True and retried.job.status == "completed"
    again = seed_poc.seed(migrated_engine, run_inference=SeedRunner(), log=lambda _: None)
    assert again.job.job_id == retried.job.job_id
    assert count_rows(session_factory, InferenceJob) == 2
    assert count_rows(session_factory, DetectionRecord) == 5


# --- Application wiring ---


@pytest.mark.parametrize(("flag", "expected"), [("1", LONDON_BRIDGE_POC), ("0", None)])
def test_app_applies_poc_flag(monkeypatch: pytest.MonkeyPatch, flag: str, expected) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:1/infrascan")
    monkeypatch.setenv("INFRASCAN_POC_GEOLOCATION", flag)
    import main

    with TestClient(main.app):
        assert main.app.state.infrascan_service.poc_geolocation is expected
