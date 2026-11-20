"""Detection-to-Issue consolidation through the worker (PostgreSQL).

Jobs run through `InferenceJobProcessor` with a mocked model whose output is
built from the real v0-mvp fixture (`v0_mvp_multi_png_output.json`); the
sample camera profile projects its boxes onto a flat road facing north.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.maintenance import backfill_issue_links as backfill_cli
from app.maintenance.backfill_issue_links import backfill_issue_links
from app.models import (
    DetectionRecord,
    EvidenceMedia,
    InferenceJob,
    InferenceJobStatus,
    InputMedia,
    Issue,
)
from app.services.inference_processing import InferenceJobProcessor
from app.services.issue_linking import (
    ISSUE_LINK_LOCK_KEY,
    IssueLinker,
    IssueMatchPolicy,
    box_iou,
    candidate_issues_query,
    haversine_m,
    search_boxes,
)
from postgres_support import run_alembic
from test_inference_workflow import (
    ANNOTATED_JPEG,
    FIXTURE_PATH,
    InMemoryMediaStorage,
    RecordingQueue,
    build_client,
    build_service,
    count_rows,
)

FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
BOXES = [detection["bbox_xyxy"] for detection in FIXTURE["detections"]]
NEAR_BOX = BOXES[1]  # Projects about 2 m ahead of the camera.
SKY_BOX = [100.0, 10.0, 200.0, 40.0]  # Above the sample camera's horizon.
CAPTURED = datetime(2026, 11, 10, 22, 30, tzinfo=UTC)
LAT, LON = 33.8823, -117.8851


def det(
    class_id: int = 3,
    confidence: float = 0.9,
    severity: str = "moderate",
    bbox: list[float] = NEAR_BOX,
) -> dict:
    return {
        "class_ids": class_id,
        "label": "fixture",
        "classification": "fixture",
        "severity": severity,
        "confidence": confidence,
        "bbox_xyxy": list(bbox),
    }


def output(*detections: dict) -> dict:
    return {**FIXTURE, "detections": list(detections)}


def make_job(
    session_factory: sessionmaker[Session],
    *,
    captured_at: datetime = CAPTURED,
    latitude: float = LAT,
    longitude: float = LON,
) -> UUID:
    with session_factory() as session, session.begin():
        job = InferenceJob(
            input_media=InputMedia(data=b"jpeg", content_type="image/jpeg", filename="road.jpg"),
            captured_at=captured_at,
            latitude=Decimal(repr(latitude)),
            longitude=Decimal(repr(longitude)),
        )
        session.add(job)
        session.flush()
        return job.job_id


def processor(
    session_factory: sessionmaker[Session],
    result: dict,
    policy: IssueMatchPolicy | None = None,
    storage: InMemoryMediaStorage | None = None,
) -> InferenceJobProcessor:
    return InferenceJobProcessor(
        session_factory,
        lambda image, name: (copy.deepcopy(result), ANNOTATED_JPEG),
        media_storage=storage or InMemoryMediaStorage(),
        issue_policy=policy,
    )


def run(
    session_factory: sessionmaker[Session],
    result: dict,
    policy: IssueMatchPolicy | None = None,
    **job: object,
) -> UUID:
    job_id = make_job(session_factory, **job)
    assert processor(session_factory, result, policy).process(job_id) == InferenceJobStatus.COMPLETED
    return job_id


def links(session_factory: sessionmaker[Session], job_id: UUID) -> list[UUID | None]:
    """Issue IDs of a job's detections, most confident first."""
    with session_factory() as session:
        return list(
            session.scalars(
                sa.select(DetectionRecord.issue_id)
                .where(DetectionRecord.job_id == job_id)
                .order_by(DetectionRecord.confidence.desc())
            )
        )


def load_issue(session_factory: sessionmaker[Session], issue_id: UUID) -> Issue:
    with session_factory() as session:
        return session.get(Issue, issue_id)


def detection_count(session_factory: sessionmaker[Session], issue_id: UUID) -> int:
    with session_factory() as session:
        return session.scalar(
            sa.select(sa.func.count()).where(DetectionRecord.issue_id == issue_id)
        )


def set_status(session_factory: sessionmaker[Session], issue_id: UUID, status: str) -> None:
    with session_factory() as session, session.begin():
        issue = session.get(Issue, issue_id)
        issue.status = status
        issue.review_reason = None


def offset_point(latitude: float, longitude: float, north_m: float, east_m: float):
    radius = 6_371_008.8
    return (
        latitude + math.degrees(north_m / radius),
        longitude + math.degrees(east_m / (radius * math.cos(math.radians(latitude)))),
    )


# --- Policy helpers (no database) ---


def test_policy_defaults_environment_and_validation() -> None:
    assert IssueMatchPolicy.from_env({}) == IssueMatchPolicy(radius_m=8.0, low_confidence=0.5)
    assert IssueMatchPolicy.from_env(
        {"INFRASCAN_ISSUE_MATCH_RADIUS_M": "5", "INFRASCAN_ISSUE_LOW_CONFIDENCE": "0.4"}
    ) == IssueMatchPolicy(radius_m=5.0, low_confidence=0.4)
    for bad in ({"INFRASCAN_ISSUE_MATCH_RADIUS_M": "0"},
                {"INFRASCAN_ISSUE_MATCH_RADIUS_M": "nan"},
                {"INFRASCAN_ISSUE_MATCH_RADIUS_M": "101"},
                {"INFRASCAN_ISSUE_MATCH_RADIUS_M": "eight"},
                {"INFRASCAN_ISSUE_LOW_CONFIDENCE": "1.5"}):
        with pytest.raises(ValueError):
            IssueMatchPolicy.from_env(bad)


@pytest.mark.parametrize("latitude", [0.0, 33.88, 60.0, -75.0])
def test_search_boxes_cover_the_radius_circle(latitude: float) -> None:
    (west, south, east, north), = search_boxes(latitude, 10.0, 8.0)
    for bearing in range(0, 360, 5):
        lat, lon = offset_point(
            latitude, 10.0,
            8.0 * math.cos(math.radians(bearing)), 8.0 * math.sin(math.radians(bearing)),
        )
        assert haversine_m(latitude, 10.0, lat, lon) == pytest.approx(8.0, abs=1e-3)
        assert south <= lat <= north and west <= lon <= east


def test_search_boxes_split_at_the_antimeridian() -> None:
    boxes = search_boxes(-17.0, 179.99995, 8.0)
    assert len(boxes) == 2
    assert boxes[0][2] == 180.0 and boxes[1][0] == -180.0
    assert haversine_m(-17.0, 179.99995, -17.0, -179.99999) < 8.0


def test_box_iou() -> None:
    assert box_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert box_iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 / 3)
    assert box_iou([0, 0, 10, 10], [10, 0, 20, 10]) == 0.0


# --- Matching through the worker ---


def test_repeat_submission_links_each_box_to_its_issue_and_widens_seen_times(
    session_factory: sessionmaker[Session],
) -> None:
    later = run(session_factory, FIXTURE, captured_at=CAPTURED + timedelta(days=1))
    earlier = run(session_factory, FIXTURE, captured_at=CAPTURED)

    assert links(session_factory, earlier) == links(session_factory, later)
    assert count_rows(session_factory, Issue) == 5
    with session_factory() as session:
        for issue in session.scalars(sa.select(Issue)):
            assert detection_count(session_factory, issue.issue_id) == 2
            assert issue.first_seen_at == CAPTURED
            assert issue.last_seen_at == CAPTURED + timedelta(days=1)
            located = session.scalars(
                sa.select(DetectionRecord.location).where(
                    DetectionRecord.issue_id == issue.issue_id
                )
            ).all()
            assert located[0] == located[1]
            assert (issue.latitude, issue.longitude) == pytest.approx(
                (located[0]["latitude"], located[0]["longitude"]), abs=1e-12
            )


def test_distinct_boxes_in_one_image_stay_separate_even_when_close(
    session_factory: sessionmaker[Session],
) -> None:
    job_id = run(session_factory, FIXTURE)

    issue_ids = links(session_factory, job_id)
    assert len(set(issue_ids)) == 5
    issues = [load_issue(session_factory, issue_id) for issue_id in issue_ids]
    distances = [
        haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
        for i, a in enumerate(issues) for b in issues[i + 1:]
    ]
    # Closer than the 8 m radius, yet never merged: they share one image.
    assert min(distances) < 4.0


def test_radius_is_great_circle_meters_not_degrees(
    session_factory: sessionmaker[Session],
) -> None:
    result = output(det())
    base = run(session_factory, result, latitude=60.0, longitude=10.0)
    east_lat, east_lon = offset_point(60.0, 10.0, 0.0, 6.0)
    east = run(session_factory, result, latitude=east_lat, longitude=east_lon)
    # Same offset in degrees, but northward: about 12 m at latitude 60.
    north = run(session_factory, result, latitude=60.0 + (east_lon - 10.0), longitude=10.0)

    assert links(session_factory, east) == links(session_factory, base)
    assert links(session_factory, north) != links(session_factory, base)
    assert count_rows(session_factory, Issue) == 2


def test_match_radius_is_configurable(session_factory: sessionmaker[Session]) -> None:
    result = output(det())
    base = run(session_factory, result)
    lat, lon = offset_point(LAT, LON, 6.0, 0.0)
    tight = run(session_factory, result, IssueMatchPolicy(radius_m=5.0), latitude=lat, longitude=lon)
    loose = run(session_factory, result, IssueMatchPolicy(radius_m=7.0), latitude=lat, longitude=lon)

    assert links(session_factory, tight) != links(session_factory, base)
    # Both potholes are within 7 m; the one the tight job created is 0 m away.
    assert links(session_factory, loose) == links(session_factory, tight)


def test_closest_issue_wins(session_factory: sessionmaker[Session]) -> None:
    result = output(det())
    near_lat, near_lon = offset_point(LAT, LON, 0.0, -2.0)
    # 9 m apart, so they stay separate; both are within 8 m of the third job.
    far_lat, far_lon = offset_point(LAT, LON, 0.0, 7.0)
    [near] = links(session_factory, run(session_factory, result, latitude=near_lat, longitude=near_lon))
    [far] = links(session_factory, run(session_factory, result, latitude=far_lat, longitude=far_lon))
    [chosen] = links(session_factory, run(session_factory, result))
    assert near != far and chosen == near


def test_equidistant_issues_tie_break_on_first_seen_then_issue_id(
    session_factory: sessionmaker[Session],
) -> None:
    # Two identical boxes in one image: two Issues at exactly the same point.
    first, second = links(session_factory, run(session_factory, output(det(), det(confidence=0.8))))
    with session_factory() as session, session.begin():
        session.get(Issue, second).first_seen_at = CAPTURED - timedelta(days=1)
    [chosen] = links(session_factory, run(session_factory, output(det())))
    assert chosen == second

    with session_factory() as session, session.begin():
        session.get(Issue, second).first_seen_at = CAPTURED
    [chosen] = links(session_factory, run(session_factory, output(det())))
    assert chosen == min(first, second)


def test_different_types_never_merge_and_a_new_neighbor_is_flagged(
    session_factory: sessionmaker[Session],
) -> None:
    first = run(session_factory, output(det(3, 0.9, "moderate"), det(2, 0.8, "critical")))
    pothole_id, alligator_id = links(session_factory, first)
    assert pothole_id != alligator_id
    pothole = load_issue(session_factory, pothole_id)
    alligator = load_issue(session_factory, alligator_id)
    assert (pothole.issue_type_code, pothole.review_reason) == ("pothole", None)
    assert (alligator.issue_type_code, alligator.review_reason) == ("alligator_crack", "type_conflict")

    second = run(session_factory, output(det(2, 0.85, "minor")))
    assert links(session_factory, second) == [alligator_id]
    alligator = load_issue(session_factory, alligator_id)
    assert alligator.review_reason == "type_conflict"
    assert alligator.severity == "critical"

    third = run(session_factory, output(det(0, 0.9)))
    [crack_id] = links(session_factory, third)
    crack = load_issue(session_factory, crack_id)
    assert crack_id not in {pothole_id, alligator_id}
    assert (crack.issue_type_code, crack.review_reason) == ("longitudinal_crack", "type_conflict")
    assert count_rows(session_factory, Issue) == 3


def test_low_confidence_flag_clears_when_a_confident_detection_arrives(
    session_factory: sessionmaker[Session],
) -> None:
    first = run(session_factory, output(det(confidence=0.3, severity="minor")))
    [issue_id] = links(session_factory, first)
    issue = load_issue(session_factory, issue_id)
    assert (issue.status, issue.review_reason, issue.severity) == (
        "pending_review", "low_confidence", "minor",
    )

    second = run(session_factory, output(det(confidence=0.8, severity="critical")))
    assert links(session_factory, second) == [issue_id]
    issue = load_issue(session_factory, issue_id)
    assert (issue.status, issue.review_reason, issue.severity) == (
        "pending_review", None, "critical",
    )

    third = run(session_factory, output(det(confidence=0.2, severity="minor")))
    assert links(session_factory, third) == [issue_id]
    issue = load_issue(session_factory, issue_id)
    assert (issue.review_reason, issue.severity) == (None, "critical")


def test_reviewed_issue_decisions_are_never_changed(session_factory: sessionmaker[Session]) -> None:
    day = timedelta(days=1)
    [accepted_id] = links(session_factory, run(session_factory, output(det(severity="minor"))))
    set_status(session_factory, accepted_id, "accepted")
    before = load_issue(session_factory, accepted_id)

    north_lat, north_lon = offset_point(LAT, LON, 2.0, 0.0)
    second = run(
        session_factory, output(det(severity="critical")),
        captured_at=CAPTURED + day, latitude=north_lat, longitude=north_lon,
    )
    assert links(session_factory, second) == [accepted_id]
    accepted = load_issue(session_factory, accepted_id)
    assert (accepted.status, accepted.review_reason, accepted.severity) == ("accepted", None, "minor")
    assert (accepted.latitude, accepted.longitude) == (before.latitude, before.longitude)
    assert (accepted.first_seen_at, accepted.last_seen_at) == (CAPTURED, CAPTURED + day)
    assert detection_count(session_factory, accepted_id) == 2

    set_status(session_factory, accepted_id, "resolved")
    third = run(session_factory, output(det()), captured_at=CAPTURED + 2 * day)
    [reopened_id] = links(session_factory, third)
    assert reopened_id != accepted_id
    resolved = load_issue(session_factory, accepted_id)
    assert (resolved.status, resolved.last_seen_at) == ("resolved", CAPTURED + day)
    assert detection_count(session_factory, accepted_id) == 2
    assert load_issue(session_factory, reopened_id).status == "pending_review"

    set_status(session_factory, reopened_id, "dismissed")
    fourth = run(session_factory, output(det()), captured_at=CAPTURED + 3 * day)
    [newest_id] = links(session_factory, fourth)
    assert newest_id not in {accepted_id, reopened_id}
    dismissed = load_issue(session_factory, reopened_id)
    assert (dismissed.status, dismissed.last_seen_at) == ("dismissed", CAPTURED + 2 * day)
    assert count_rows(session_factory, Issue) == 3


def test_unlocated_detections_are_reviewable_and_never_merged_across_submissions(
    session_factory: sessionmaker[Session],
) -> None:
    result = output(det(confidence=0.9, bbox=SKY_BOX), det(confidence=0.8, bbox=SKY_BOX))
    first = run(session_factory, result)
    second = run(session_factory, result)

    issue_ids = links(session_factory, first) + links(session_factory, second)
    assert len(set(issue_ids)) == 4
    with session_factory() as session:
        assert session.scalar(
            sa.select(sa.func.count()).where(DetectionRecord.location.is_not(None))
        ) == 0
    for issue_id in issue_ids:
        issue = load_issue(session_factory, issue_id)
        assert (issue.latitude, issue.longitude, issue.location_accuracy_m) == (None, None, None)
        assert (issue.status, issue.review_reason) == ("pending_review", "missing_location")
        assert issue.first_seen_at == issue.last_seen_at == CAPTURED

    with build_client(build_service(session_factory, RecordingQueue(session_factory))) as client:
        queue = client.get("/api/v1/issues/review-queue").json()
        world = client.get("/api/v1/issues/map", params={
            "min_latitude": -90, "min_longitude": -180, "max_latitude": 90, "max_longitude": 180,
        }).json()
    assert {item["issue_id"] for item in queue["items"]} == {str(i) for i in issue_ids}
    assert world["features"] == []


def test_unmapped_model_class_is_saved_without_an_issue(
    session_factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        job_id = run(session_factory, output(det(9, 0.95), det(3, 0.8)))

    unmapped, pothole = links(session_factory, job_id)
    assert unmapped is None and pothole is not None
    assert count_rows(session_factory, Issue) == 1
    assert any(
        "unmapped model class 9" in record.getMessage() and str(job_id) in record.getMessage()
        for record in caplog.records
    )
    with build_client(build_service(session_factory, RecordingQueue(session_factory))) as client:
        items = client.get(f"/api/v1/jobs/{job_id}/detections").json()["items"]
    assert [(i["issue_type"], i["predicted_issue_type_code"], i["issue_id"]) for i in items] == [
        (9, None, None), (3, "pothole", str(pothole)),
    ]


# --- Transactions, retries, and concurrency ---


def test_linking_failure_rolls_back_everything_and_deletes_evidence(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = IssueLinker._create
    calls = {"count": 0}

    def fail_second(self, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("issue insert failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(IssueLinker, "_create", fail_second)
    storage = InMemoryMediaStorage()
    job_id = make_job(session_factory)
    status = processor(session_factory, FIXTURE, storage=storage).process(job_id)

    assert status == InferenceJobStatus.FAILED
    for model in (Issue, DetectionRecord, EvidenceMedia):
        assert count_rows(session_factory, model) == 0
    assert storage.objects == {} and storage.deletes == storage.puts
    with session_factory() as session:
        job = session.get(InferenceJob, job_id)
        assert (job.status, job.completed_at) == ("failed", None)


def test_duplicate_celery_delivery_does_not_add_detections(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from inference.celery import tasks

    monkeypatch.setattr(tasks, "R2MediaStorageAdapter", InMemoryMediaStorage)
    monkeypatch.setattr(tasks.database, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        tasks, "run_v0_mvp_inference", lambda image, name: (copy.deepcopy(FIXTURE), ANNOTATED_JPEG)
    )
    job_id = make_job(session_factory)
    tasks.process_inference_job(str(job_id))
    snapshot = links(session_factory, job_id)
    tasks.process_inference_job(str(job_id))

    assert links(session_factory, job_id) == snapshot
    assert count_rows(session_factory, DetectionRecord) == 5
    assert count_rows(session_factory, Issue) == 5


def test_concurrent_jobs_for_the_same_defect_share_issues(
    session_factory: sessionmaker[Session],
) -> None:
    barrier = threading.Barrier(2, timeout=20)

    def runner(image: bytes, name: str | None):
        barrier.wait()  # Both jobs finish inference together, then race to save.
        return copy.deepcopy(FIXTURE), ANNOTATED_JPEG

    job_ids = [make_job(session_factory) for _ in range(2)]
    results: dict[UUID, object] = {}

    def work(job_id: UUID) -> None:
        results[job_id] = InferenceJobProcessor(
            session_factory, runner, media_storage=InMemoryMediaStorage()
        ).process(job_id)

    threads = [threading.Thread(target=work, args=(job_id,)) for job_id in job_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert results == {job_id: InferenceJobStatus.COMPLETED for job_id in job_ids}
    assert links(session_factory, job_ids[0]) == links(session_factory, job_ids[1])
    assert count_rows(session_factory, Issue) == 5
    for issue_id in links(session_factory, job_ids[0]):
        assert detection_count(session_factory, issue_id) == 2


def test_matching_waits_on_the_advisory_lock_only_while_saving(
    session_factory: sessionmaker[Session], migrated_engine: sa.Engine
) -> None:
    inferred = threading.Event()

    def runner(image: bytes, name: str | None):
        inferred.set()
        return copy.deepcopy(FIXTURE), ANNOTATED_JPEG

    job_id = make_job(session_factory)
    outcome: list[object] = []
    holder = migrated_engine.connect()
    transaction = holder.begin()
    holder.execute(sa.select(sa.func.pg_advisory_xact_lock(ISSUE_LINK_LOCK_KEY)))
    worker = threading.Thread(
        target=lambda: outcome.append(
            InferenceJobProcessor(
                session_factory, runner, media_storage=InMemoryMediaStorage()
            ).process(job_id)
        )
    )
    try:
        worker.start()
        # Inference is not blocked by the held lock; only the result save waits.
        assert inferred.wait(timeout=20)
        deadline = time.monotonic() + 20
        waiting = 0
        while time.monotonic() < deadline and not waiting:
            with migrated_engine.connect() as probe:
                waiting = probe.scalar(sa.text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                ))
            time.sleep(0.05)
        assert waiting == 1
        with session_factory() as session:
            assert session.get(InferenceJob, job_id).status == "running"
        assert count_rows(session_factory, DetectionRecord) == 0
    finally:
        transaction.rollback()
        holder.close()
        worker.join(timeout=30)
    assert outcome == [InferenceJobStatus.COMPLETED]
    assert count_rows(session_factory, Issue) == 5


def test_candidate_query_uses_the_location_index(session_factory: sessionmaker[Session]) -> None:
    query = candidate_issues_query(LAT, LON, 8.0)
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    with session_factory() as session:
        session.execute(sa.text("SET LOCAL enable_seqscan = off"))
        plan = session.execute(sa.text(f"EXPLAIN {sql}")).scalars().all()
    assert any("ix_issues_location" in line for line in plan), plan


# --- Historical backfill ---


def _historical_job(
    session: Session, captured_at: datetime, frame_timestamp: float | None = None
) -> tuple[InferenceJob, EvidenceMedia]:
    job = InferenceJob(
        input_media=InputMedia(data=b"jpeg", content_type="image/jpeg", filename="old.jpg"),
        status="completed",
        model_version="v0-mvp",
        captured_at=captured_at,
        latitude=Decimal(repr(LAT)),
        longitude=Decimal(repr(LON)),
    )
    evidence = EvidenceMedia(
        job=job, data=b"old-evidence", content_type="image/jpeg", frame_timestamp=frame_timestamp
    )
    session.add_all([job, evidence])
    return job, evidence


def _historical_detection(
    session: Session,
    job: InferenceJob,
    evidence: EvidenceMedia,
    *,
    class_id: int = 3,
    location: dict | None = None,
    confidence: float = 0.9,
) -> UUID:
    record = DetectionRecord(
        detection_id=uuid4(),
        job=job,
        evidence=evidence,
        issue_type=class_id,
        confidence=confidence,
        severity="moderate",
        bounding_box=[1.0, 2.0, 3.0, 4.0],
        location=location,
        frame_timestamp=evidence.frame_timestamp,
        detected_at=job.captured_at + timedelta(hours=1),
        observed_at=None,
    )
    session.add(record)
    return record.detection_id


def test_backfill_applies_the_same_policy_and_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    worker_job = run(session_factory, output(det()))
    [existing_id] = links(session_factory, worker_job)
    existing = load_issue(session_factory, existing_id)
    point = {"latitude": existing.latitude, "longitude": existing.longitude}
    near_lat, near_lon = offset_point(existing.latitude, existing.longitude, 3.0, 0.0)
    old_capture = CAPTURED - timedelta(days=30)

    with session_factory() as session, session.begin():
        job, evidence = _historical_job(session, old_capture)
        same_spot = _historical_detection(session, job, evidence, location=point)
        same_frame = _historical_detection(
            session, job, evidence, location={"latitude": near_lat, "longitude": near_lon},
            confidence=0.7,
        )
        unmapped = _historical_detection(session, job, evidence, class_id=9, location=point)
        unlocated = _historical_detection(session, job, evidence, location=None, confidence=0.6)
        video_job, frame = _historical_job(session, old_capture, frame_timestamp=1.5)
        video = _historical_detection(session, video_job, frame, location=point)

    with session_factory() as session, session.begin():
        report = backfill_issue_links(session, IssueMatchPolicy())
    assert report.as_dict() == {
        "jobs": 2,
        "linked_detections": 3,
        "created_issues": 2,
        "updated_existing_issues": 1,
        "skipped": {
            "unknown_observation_time": [str(video)],
            "unmapped_class": [str(unmapped)],
        },
    }

    with session_factory() as session:
        records = {
            record.detection_id: record
            for record in session.scalars(
                sa.select(DetectionRecord).where(DetectionRecord.job_id.in_([job.job_id, video_job.job_id]))
            )
        }
        assert records[same_spot].issue_id == existing_id
        assert records[same_spot].observed_at == old_capture
        # Within the radius of the existing Issue, but from the same frame.
        assert records[same_frame].issue_id not in {None, existing_id}
        assert records[unmapped].issue_id is None
        assert records[video].issue_id is None and records[video].observed_at is None
        unlocated_issue = session.get(Issue, records[unlocated].issue_id)
        assert unlocated_issue.review_reason == "missing_location"
    refreshed = load_issue(session_factory, existing_id)
    assert (refreshed.first_seen_at, refreshed.last_seen_at) == (old_capture, CAPTURED)
    assert detection_count(session_factory, existing_id) == 2

    with session_factory() as session, session.begin():
        again = backfill_issue_links(session, IssueMatchPolicy()).as_dict()
    assert again["linked_detections"] == again["created_issues"] == 0
    assert again["skipped"] == report.as_dict()["skipped"]
    assert count_rows(session_factory, Issue) == 3


def test_backfill_command_defaults_to_a_dry_run(
    session_factory: sessionmaker[Session],
    migrated_engine: sa.Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with session_factory() as session, session.begin():
        job, evidence = _historical_job(session, CAPTURED)
        _historical_detection(session, job, evidence, location={"latitude": LAT, "longitude": LON})
    monkeypatch.setenv("DATABASE_URL", migrated_engine.url.render_as_string(hide_password=False))

    backfill_cli.main([])
    dry = json.loads(capsys.readouterr().out)
    assert (dry["applied"], dry["linked_detections"], dry["created_issues"]) == (False, 1, 1)
    assert count_rows(session_factory, Issue) == 0

    backfill_cli.main(["--apply"])
    assert json.loads(capsys.readouterr().out)["linked_detections"] == 1
    assert count_rows(session_factory, Issue) == 1

    backfill_cli.main(["--apply"])
    assert json.loads(capsys.readouterr().out)["linked_detections"] == 0


def test_observed_at_migration_fills_only_still_image_rows(disposable_postgres_url: str) -> None:
    url = disposable_postgres_url
    assert run_alembic("upgrade", "c3e5f7a92041", database_url=url).returncode == 0
    from app.db import create_db_engine

    engine = create_db_engine(url)
    try:
        with engine.begin() as conn:
            media_id = conn.execute(sa.text(
                "INSERT INTO input_media (data, content_type) VALUES ('\\xffd8'::bytea, 'image/jpeg') "
                "RETURNING media_id"
            )).scalar_one()
            job_id = conn.execute(sa.text(
                "INSERT INTO inference_jobs (input_media_id, captured_at, latitude, longitude) "
                "VALUES (:m, '2026-11-10T22:30:00Z', 33.8, -117.9) RETURNING job_id"
            ), {"m": media_id}).scalar_one()
            for frame_timestamp in (None, 2.0):
                conn.execute(sa.text(
                    "INSERT INTO detection_records (job_id, issue_type, confidence, severity, "
                    "bounding_box, frame_timestamp, detected_at) VALUES (:j, 3, 0.9, 'minor', "
                    "'[1, 2, 3, 4]', :f, '2026-11-12T00:00:00Z')"
                ), {"j": job_id, "f": frame_timestamp})

        up = run_alembic("upgrade", "head", database_url=url)
        assert up.returncode == 0, up.stderr
        with engine.connect() as conn:
            rows = conn.execute(sa.text(
                "SELECT frame_timestamp, observed_at FROM detection_records "
                "ORDER BY frame_timestamp NULLS FIRST"
            )).all()
        assert rows == [(None, datetime(2026, 11, 10, 22, 30, tzinfo=UTC)), (2.0, None)]

        down = run_alembic("downgrade", "c3e5f7a92041", database_url=url)
        assert down.returncode == 0, down.stderr
        with engine.connect() as conn:
            columns = conn.execute(sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'detection_records'"
            )).scalars().all()
        assert "observed_at" not in columns
    finally:
        engine.dispose()
