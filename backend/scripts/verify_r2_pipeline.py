"""Exercise the live API, Celery worker, PostgreSQL and R2 with synthetic media.

Run inside the configured API container: python -m scripts.verify_r2_pipeline
Only objects and rows created by this invocation are removed on success.
"""
from io import BytesIO
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from PIL import Image
import sqlalchemy as sa
from app.adapters.r2_storage import R2MediaStorageAdapter
from app.db import create_db_engine, create_session_factory
from app.models import InputMedia, InferenceJob, EvidenceMedia, DetectionRecord


def main():
    base = os.environ.get("VERIFY_API_URL", "http://localhost:8000")
    engine = create_db_engine()
    factory = create_session_factory(engine)
    storage = R2MediaStorageAdapter()
    created = []
    keys = []
    report = []
    def get(path):
        with urlopen(base + path, timeout=30) as response:
            return json.load(response)
    with tempfile.TemporaryDirectory(prefix="infrascan-live-") as directory:
        image = BytesIO()
        Image.new("RGB", (640, 480), (128, 128, 128)).save(image, format="JPEG")
        video = Path(directory) / "clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=160x120:rate=4", "-t", "3", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(video)], check=True)
        for filename, payload, mime in [("check.jpg", image.getvalue(), "image/jpeg"),
                                        ("check.mp4", video.read_bytes(), "video/mp4")]:
            boundary = uuid4().hex
            metadata = json.dumps({"captured_at": "2026-11-19T12:00:00-08:00", "latitude": 33.8823, "longitude": -117.8851})
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
                    + payload + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n\r\n{metadata}\r\n--{boundary}--\r\n'.encode())
            started = time.monotonic()
            request = Request(base + "/api/v1/inference/jobs", data=body,
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urlopen(request, timeout=120) as response:
                assert response.status == 202
                accepted = json.load(response)
            job_id = UUID(accepted["job_id"])
            created.append(job_id)
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                job = get(f"/api/v1/jobs/{job_id}")
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(1)
            assert job["status"] == "completed", {"job_id": str(job_id), "status": job["status"]}
            detections = get(f"/api/v1/jobs/{job_id}/detections")
            with factory() as session:
                row = session.get(InferenceJob, job_id)
                assert row.input_media.data is None
                assert storage.get(row.input_media.object_storage_key) == payload
                keys.append(row.input_media.object_storage_key)
                evidence = session.scalars(sa.select(EvidenceMedia).where(EvidenceMedia.job_id == job_id)).all()
                assert len(evidence) == (12 if mime == "video/mp4" else 1)
                for ev in evidence:
                    assert ev.data is None
                    assert storage.get(ev.object_storage_key).startswith(b"\xff\xd8")
                    keys.append(ev.object_storage_key)
                if mime == "video/mp4":
                    assert sorted(e.frame_timestamp for e in evidence) == [i / 4 for i in range(12)]
                    records = session.scalars(sa.select(DetectionRecord).where(DetectionRecord.job_id == job_id)).all()
                    assert all(r.location is None and r.frame_timestamp is not None for r in records)
            item = {"media": mime, "job_id": str(job_id), "status": job["status"],
                    "evidence_objects": len(evidence), "detections": detections["total"],
                    "elapsed_seconds": round(time.monotonic() - started, 2)}
            report.append(item)
            print(json.dumps(item), flush=True)
    # Exercise the multipart threshold separately from the short valid clips.
    multipart_key = f"verification/{uuid4()}/multipart.bin"
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as target:
        for _ in range(9):
            source.write(b"x" * 1024 * 1024)
        source.seek(0)
        storage.put_file(source, multipart_key, "application/octet-stream")
        keys.append(multipart_key)
        storage.download_to(multipart_key, target, 9 * 1024 * 1024)
        target.seek(0)
        source.seek(0)
        assert hashlib.file_digest(source, "sha256").digest() == hashlib.file_digest(target, "sha256").digest()
    print(json.dumps({"multipart_round_trip_bytes": 9 * 1024 * 1024}), flush=True)
    # All generated records are in this isolated verification database.
    with factory() as session, session.begin():
        media_ids = list(session.scalars(sa.select(InferenceJob.input_media_id).where(InferenceJob.job_id.in_(created))))
        for model in (DetectionRecord, EvidenceMedia, InferenceJob):
            session.execute(sa.delete(model).where(model.job_id.in_(created)))
        session.execute(sa.delete(InputMedia).where(InputMedia.media_id.in_(media_ids)))
    for key in keys:
        storage.delete(key)
    engine.dispose()
    print(json.dumps({"verified": report, "generated_objects_removed": len(keys)}))


if __name__ == "__main__":
    main()
