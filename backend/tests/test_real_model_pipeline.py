"""Real v0-mvp model path. Skipped unless ultralytics and the weights are available.

Unlike `test_inference_workflow.py`, nothing here mocks the model: these
tests load `inference/weights/best.pt` and run YOLO on CPU.
"""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import time
from uuid import UUID

import pytest

pytest.importorskip("ultralytics")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from inference import service as inference  # noqa: E402
from inference.labels import CLASS_LABELS  # noqa: E402

if not Path(inference.DEFAULT_WEIGHT_PATH).is_file():
    pytest.skip("Shipped v0-mvp weights are missing", allow_module_level=True)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v0_mvp_multi_png_output.json"
SMOKE_IMAGE = Path(inference.DEFAULT_IMAGE_PATH)
DETECTION_KEYS = {"class_ids", "label", "classification", "severity", "confidence", "bbox_xyxy"}


def _jpeg(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_service_uses_central_label_map() -> None:
    assert inference.label_map is CLASS_LABELS
    assert inference.MODEL_NAME == "infrascan-rdd2022-yolo11s"
    assert inference.MODEL_VERSION == "v0-mvp"


def test_real_inference_returns_v0_contract_and_in_memory_evidence() -> None:
    output_mtime = inference.OUTPUT_PATH.stat().st_mtime_ns if inference.OUTPUT_PATH.exists() else None

    result, annotated = inference.inference_with_annotation(
        inference.DEFAULT_WEIGHT_PATH, SMOKE_IMAGE.read_bytes(), filename=SMOKE_IMAGE.name
    )

    assert set(result) == {"image_width", "image_height", "model_name", "model_version", "detections"}
    assert (result["image_width"], result["image_height"]) == (886, 595)
    assert result["model_version"] == "v0-mvp"
    assert result["detections"]
    for detection in result["detections"]:
        assert set(detection) == DETECTION_KEYS
        assert type(detection["class_ids"]) is int
        assert detection["classification"] == CLASS_LABELS[detection["class_ids"]]
        assert detection["confidence"] >= inference.CONF_THRESHOLD
        assert len(detection["bbox_xyxy"]) == 4

    # The committed fixture used by mocked tests reflects this real output.
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert [d["class_ids"] for d in result["detections"]] == [
        d["class_ids"] for d in fixture["detections"]
    ]
    for real, recorded in zip(result["detections"], fixture["detections"]):
        assert real["bbox_xyxy"] == pytest.approx(recorded["bbox_xyxy"], abs=0.5)
        assert real["confidence"] == pytest.approx(recorded["confidence"], abs=1e-3)

    with Image.open(BytesIO(annotated)) as evidence:
        assert evidence.format == "JPEG"
        assert evidence.size == (886, 595)

    # The worker path never writes the shared smoke-test output file.
    after = inference.OUTPUT_PATH.stat().st_mtime_ns if inference.OUTPUT_PATH.exists() else None
    assert after == output_mtime


def test_exif_rotated_upload_is_analyzed_upright() -> None:
    upright = Image.open(SMOKE_IMAGE).convert("RGB")
    # Stored rotated 90 deg counterclockwise; EXIF orientation 6 says to
    # rotate it clockwise for display, which restores the upright image.
    stored = upright.transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[274] = 6
    buffer = BytesIO()
    stored.save(buffer, format="JPEG", quality=95, exif=exif)

    rotated_result, annotated = inference.inference_with_annotation(
        inference.DEFAULT_WEIGHT_PATH, buffer.getvalue()
    )
    upright_result, _ = inference.inference_with_annotation(
        inference.DEFAULT_WEIGHT_PATH, _jpeg(upright)
    )

    assert (rotated_result["image_width"], rotated_result["image_height"]) == (886, 595)
    with Image.open(BytesIO(annotated)) as evidence:
        assert evidence.size == (886, 595)
    top_rotated = max(rotated_result["detections"], key=lambda d: d["confidence"])
    top_upright = max(upright_result["detections"], key=lambda d: d["confidence"])
    assert top_rotated["bbox_xyxy"] == pytest.approx(top_upright["bbox_xyxy"], abs=4.0)


def test_real_inference_on_blank_image_has_zero_detections() -> None:
    blank = _jpeg(Image.new("RGB", (640, 480), color=(128, 128, 128)))

    result, annotated = inference.inference_with_annotation(inference.DEFAULT_WEIGHT_PATH, blank)

    assert result["detections"] == []
    assert annotated.startswith(b"\xff\xd8")


def test_http_to_celery_worker_to_database_with_real_model(
    session_factory: sessionmaker[Session],
    migrated_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from celery.contrib.testing.worker import start_worker

    from app.adapters.celery_queue import CeleryInferenceQueue
    from app.api.v1.router import api_router
    from app.geolocation import LONDON_BRIDGE_POC
    from app.models import InferenceJob
    from app.services.inference_jobs import DatabaseInfraScanService
    from inference.celery import database
    from inference.celery import tasks  # noqa: F401  registers the production task
    from inference.celery.celery_app import celery_app

    monkeypatch.setenv("DATABASE_URL", migrated_engine.url.render_as_string(hide_password=False))
    database.dispose_engine()
    celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://")

    app = FastAPI()
    app.state.infrascan_service = DatabaseInfraScanService(
        session_factory, CeleryInferenceQueue(celery_app), poc_geolocation=LONDON_BRIDGE_POC
    )
    app.include_router(api_router, prefix="/api/v1")
    image = _jpeg(Image.open(SMOKE_IMAGE))
    metadata = {"captured_at": "2026-11-10T14:30:00-08:00", "latitude": 33.8823, "longitude": -117.8851}

    try:
        with start_worker(celery_app, pool="solo", perform_ping_check=False, shutdown_timeout=30):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/inference/jobs",
                    files=[
                        ("file", ("multi.jpg", image, "image/jpeg")),
                        ("metadata", (None, json.dumps(metadata))),
                    ],
                )
                assert response.status_code == 202
                job_id = UUID(response.json()["job_id"])

                deadline = time.monotonic() + 90
                status = "pending"
                while status in {"pending", "running"} and time.monotonic() < deadline:
                    time.sleep(0.5)
                    status = client.get(f"/api/v1/jobs/{job_id}").json()["status"]

                job = client.get(f"/api/v1/jobs/{job_id}").json()
                detections = client.get(f"/api/v1/jobs/{job_id}/detections").json()
                annotated = client.get(job["annotated_image_url"])
    finally:
        database.dispose_engine()

    assert job["status"] == "completed"
    assert job["model_version"] == "v0-mvp"
    assert (job["metadata"]["latitude"], job["metadata"]["longitude"]) == (51.507921, -0.0877)
    assert job["geolocation"]["mode"] == "poc_assumed"
    assert annotated.status_code == 200
    assert annotated.headers["content-type"] == "image/jpeg"
    assert detections["total"] == len(detections["items"]) > 0
    evidence_ids = {d["evidence_media"] for d in detections["items"]}
    assert len(evidence_ids) == 1 and None not in evidence_ids
    for item in detections["items"]:
        assert item["classification"] == CLASS_LABELS[item["issue_type"]]
        assert len(item["bounding_box"]) == 4
        assert item["location_status"] == "estimated"
        assert abs(item["latitude"] - 51.507921) < 0.001
        assert abs(item["longitude"] + 0.0877) < 0.001

    # Compare persisted rows with a direct call on the same stored bytes.
    expected, _ = inference.inference_with_annotation(inference.DEFAULT_WEIGHT_PATH, image)
    with session_factory() as session:
        stored = session.get(InferenceJob, job_id)
        assert stored.input_media.data == image
        rows = sorted(stored.detection_records, key=lambda r: r.confidence, reverse=True)
        assert [r.bounding_box for r in rows] == [d["bbox_xyxy"] for d in expected["detections"]]
        assert [r.confidence for r in rows] == [d["confidence"] for d in expected["detections"]]
        assert [r.severity for r in rows] == [d["severity"] for d in expected["detections"]]
        with Image.open(BytesIO(stored.evidence_media[0].data)) as evidence:
            assert evidence.format == "JPEG"
            assert evidence.size == Image.open(BytesIO(image)).size
        assert annotated.content == stored.evidence_media[0].data


def test_seed_command_with_real_model_is_repeatable(
    session_factory: sessionmaker[Session], migrated_engine
) -> None:
    from app import seed_poc
    from app.models import DetectionRecord, InferenceJob

    first = seed_poc.seed(migrated_engine, log=lambda _: None)
    second = seed_poc.seed(migrated_engine, log=lambda _: None)

    assert first.created and not second.created
    assert second.job.job_id == first.job.job_id
    assert second.job.status == "completed"
    assert second.job.annotated_media_id is not None

    # Detections are the model's output on the submitted JPEG copy.
    submitted = seed_poc.jpeg_copy(seed_poc.DEFAULT_SOURCE)
    expected, _ = inference.inference_with_annotation(inference.DEFAULT_WEIGHT_PATH, submitted)
    assert len(second.detections) == len(expected["detections"]) > 0
    assert all(d.location_status == "estimated" for d in second.detections)
    with session_factory() as session:
        assert session.query(InferenceJob).count() == 1
        assert session.query(DetectionRecord).count() == len(expected["detections"])
