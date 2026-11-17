"""Submit a bounded image batch to a running InfraScan stack and report metrics.

Run from backend/: ../.venv/bin/python scripts/load_system_metrics.py --jobs 24
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import statistics
import time

import httpx
from PIL import Image


SAMPLE_IMAGE = Path(__file__).resolve().parents[1] / "inference/data/uploads/multi.png"


def seconds(later: str, earlier: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    values = sorted(values)
    return {
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "p95": round(values[max(0, int(0.95 * len(values) + 0.999999) - 1)], 3),
        "max": round(values[-1], 3),
    }


def maximum_overlap(jobs: list[dict]) -> int:
    events = []
    for job in jobs:
        if job.get("started_at") and job.get("completed_at"):
            events.append((datetime.fromisoformat(job["started_at"]), 1))
            events.append((datetime.fromisoformat(job["completed_at"]), -1))
    active = peak = 0
    for _, change in sorted(events):
        active += change
        peak = max(peak, active)
    return peak


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--jobs", type=int, default=24)
    parser.add_argument("--submit-concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.jobs < 1 or args.submit_concurrency < 1:
        parser.error("jobs and submit-concurrency must be positive")

    image = BytesIO()
    with Image.open(SAMPLE_IMAGE) as source:
        source.convert("RGB").save(image, format="JPEG", quality=90)
    jpeg = image.getvalue()
    metadata = json.dumps({
        "captured_at": datetime.now().astimezone().isoformat(),
        "latitude": 33.8823, "longitude": -117.8851,
    })
    started = time.monotonic()
    accepted = []
    submission_errors = []
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        def submit(index: int) -> tuple[int, httpx.Response]:
            return index, client.post(
                "/api/v1/inference/jobs",
                files={"file": (f"load-{index}.jpg", jpeg, "image/jpeg"),
                       "metadata": (None, metadata)},
            )

        with ThreadPoolExecutor(max_workers=args.submit_concurrency) as pool:
            futures = [pool.submit(submit, i) for i in range(args.jobs)]
            for future in as_completed(futures):
                try:
                    index, response = future.result()
                    if response.status_code == 202:
                        accepted.append(response.json()["job_id"])
                    else:
                        submission_errors.append({"index": index, "status": response.status_code,
                                                  "body": response.text[:300]})
                except Exception as exc:
                    submission_errors.append({"error": str(exc)})
        submission_seconds = time.monotonic() - started

        jobs = {}
        deadline = started + args.timeout
        while time.monotonic() < deadline:
            for job_id in accepted:
                if jobs.get(job_id, {}).get("status") in {"completed", "failed"}:
                    continue
                response = client.get(f"/api/v1/jobs/{job_id}")
                response.raise_for_status()
                jobs[job_id] = response.json()
            if all(jobs.get(job_id, {}).get("status") in {"completed", "failed"}
                   for job_id in accepted):
                break
            time.sleep(0.5)

        detection_count = located_count = 0
        for job_id, job in jobs.items():
            if job["status"] != "completed":
                continue
            page = 1
            while True:
                response = client.get(
                    f"/api/v1/jobs/{job_id}/detections", params={"page": page, "page_size": 100}
                )
                response.raise_for_status()
                body = response.json()
                detection_count += len(body["items"])
                located_count += sum(item["location"] is not None for item in body["items"])
                if page * 100 >= body["total"]:
                    break
                page += 1

    completed = [j for j in jobs.values() if j["status"] == "completed"]
    failed = [j for j in jobs.values() if j["status"] == "failed"]
    terminal = len(completed) + len(failed)
    report = {
        "requested_jobs": args.jobs,
        "submit_concurrency": args.submit_concurrency,
        "accepted_jobs": len(accepted),
        "submission_errors": submission_errors,
        "submission_seconds": round(submission_seconds, 3),
        "total_elapsed_seconds": round(time.monotonic() - started, 3),
        "statuses": {status: sum(j["status"] == status for j in jobs.values())
                     for status in ("pending", "running", "completed", "failed")},
        "success_rate_terminal": len(completed) / terminal if terminal else None,
        "queue_wait_seconds": summary([seconds(j["started_at"], j["submitted_at"])
                                       for j in completed]),
        "processing_seconds": summary([seconds(j["completed_at"], j["started_at"])
                                       for j in completed]),
        "end_to_end_seconds": summary([seconds(j["completed_at"], j["submitted_at"])
                                       for j in completed]),
        "max_overlapping_jobs": maximum_overlap(completed),
        "detections": detection_count,
        "located_detections": located_count,
        "geolocation_yield": located_count / detection_count if detection_count else None,
        "accepted_job_ids": accepted,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
