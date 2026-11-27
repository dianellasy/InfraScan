"""Seed synthetic canonical Issues along a Stanton-to-Anaheim drive.

Run from backend/ with DATABASE_URL set:

    ../.venv/bin/python -m app.seeds.stanton_anaheim

Re-running replaces the previously seeded rows; all IDs are deterministic.
The route roughly follows Katella Ave east from Stanton, then Harbor Blvd
north into Anaheim. Coordinates are synthetic, not surveyed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import math
from uuid import NAMESPACE_URL, UUID, uuid5

from PIL import Image, ImageDraw
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import create_db_engine, create_session_factory
from app.models import (
    DETECTION_SEVERITIES,
    MODEL_CLASS_ISSUE_TYPES,
    DetectionRecord,
    EvidenceMedia,
    InferenceJob,
    InferenceJobStatus,
    InputMedia,
    Issue,
    IssueReviewReason,
    IssueStatus,
    IssueTypeCode,
)
from inference.labels import class_label

SEED_NAMESPACE = uuid5(NAMESPACE_URL, "https://infrascan.local/seeds/stanton-anaheim")
MODEL_VERSION = "synthetic-seed-stanton-anaheim"

# (latitude, longitude)
ROUTE_WAYPOINTS: tuple[tuple[float, float], ...] = (
    (33.80250, -117.99320),  # Stanton, Katella Ave near Beach Blvd
    (33.80270, -117.96000),  # Katella Ave
    (33.80300, -117.93000),  # Katella Ave
    (33.80310, -117.91460),  # Katella Ave at Harbor Blvd
    (33.81800, -117.91460),  # Harbor Blvd
    (33.83530, -117.91450),  # Anaheim
)

FIRST_PASS_AT = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)
PASS_INTERVAL = timedelta(days=7)
DRIVE_DURATION = timedelta(minutes=25)
GPS_ACCURACY_M = 4.0
METERS_PER_DEGREE_LATITUDE = 111_320.0
# Repeated detections of one Issue land a few meters apart (north, east).
DETECTION_OFFSETS_M = ((0.0, 0.0), (2.5, -1.5), (-2.0, 2.0), (1.5, 2.5))
EVIDENCE_SIZE = (320, 200)
CLASS_COLORS = {0: (225, 180, 40), 1: (240, 130, 30), 2: (200, 60, 60), 3: (110, 50, 160)}


@dataclass(frozen=True)
class SeedDetection:
    class_id: int
    confidence: float
    severity: str
    located: bool = True


@dataclass(frozen=True)
class SeedIssue:
    key: str
    route_fraction: float
    issue_type: IssueTypeCode
    status: IssueStatus
    review_reason: IssueReviewReason | None
    detections: tuple[SeedDetection, ...]

    @property
    def located(self) -> bool:
        return any(d.located for d in self.detections)


SEED_ISSUES: tuple[SeedIssue, ...] = (
    SeedIssue(
        "katella-pothole-repeated", 0.08, IssueTypeCode.POTHOLE,
        IssueStatus.ACCEPTED, None,
        (SeedDetection(3, 0.91, "critical"), SeedDetection(3, 0.88, "critical"),
         SeedDetection(3, 0.84, "moderate")),
    ),
    SeedIssue(
        "katella-longitudinal", 0.20, IssueTypeCode.LONGITUDINAL_CRACK,
        IssueStatus.ACCEPTED, None,
        (SeedDetection(0, 0.77, "minor"), SeedDetection(0, 0.72, "minor")),
    ),
    SeedIssue(
        "katella-transverse", 0.33, IssueTypeCode.TRANSVERSE_CRACK,
        IssueStatus.ACCEPTED, None,
        (SeedDetection(1, 0.81, "moderate"),),
    ),
    SeedIssue(
        "katella-alligator-repeated", 0.45, IssueTypeCode.ALLIGATOR_CRACK,
        IssueStatus.ACCEPTED, None,
        (SeedDetection(2, 0.86, "critical"), SeedDetection(2, 0.83, "moderate"),
         SeedDetection(2, 0.79, "moderate"), SeedDetection(2, 0.75, "moderate")),
    ),
    SeedIssue(
        "katella-type-conflict", 0.56, IssueTypeCode.POTHOLE,
        IssueStatus.PENDING_REVIEW, IssueReviewReason.TYPE_CONFLICT,
        (SeedDetection(3, 0.74, "moderate"), SeedDetection(2, 0.69, "moderate"),
         SeedDetection(3, 0.66, "minor")),
    ),
    SeedIssue(
        "harbor-pothole-resolved", 0.68, IssueTypeCode.POTHOLE,
        IssueStatus.RESOLVED, None,
        (SeedDetection(3, 0.80, "minor"),),
    ),
    SeedIssue(
        "harbor-longitudinal-low-confidence", 0.80, IssueTypeCode.LONGITUDINAL_CRACK,
        IssueStatus.PENDING_REVIEW, IssueReviewReason.LOW_CONFIDENCE,
        (SeedDetection(0, 0.36, "moderate"), SeedDetection(0, 0.31, "minor")),
    ),
    SeedIssue(
        "harbor-alligator", 0.93, IssueTypeCode.ALLIGATOR_CRACK,
        IssueStatus.ACCEPTED, None,
        (SeedDetection(2, 0.82, "moderate"), SeedDetection(2, 0.78, "moderate")),
    ),
    SeedIssue(
        "unlocated-pothole", 0.50, IssueTypeCode.POTHOLE,
        IssueStatus.PENDING_REVIEW, IssueReviewReason.MISSING_LOCATION,
        (SeedDetection(3, 0.87, "critical", located=False),
         SeedDetection(3, 0.83, "critical", located=False)),
    ),
    SeedIssue(
        "unlocated-transverse", 0.75, IssueTypeCode.TRANSVERSE_CRACK,
        IssueStatus.PENDING_REVIEW, IssueReviewReason.MISSING_LOCATION,
        (SeedDetection(1, 0.71, "minor", located=False),),
    ),
)


@dataclass(frozen=True)
class SeedSummary:
    issues: int
    located_issues: int
    unlocated_issues: int
    detections: int


def seed_id(kind: str, *parts: object) -> UUID:
    return uuid5(SEED_NAMESPACE, "/".join([kind, *map(str, parts)]))


def route_bounds() -> tuple[float, float, float, float]:
    """(min_latitude, min_longitude, max_latitude, max_longitude) of the route."""
    latitudes = [lat for lat, _ in ROUTE_WAYPOINTS]
    longitudes = [lon for _, lon in ROUTE_WAYPOINTS]
    return min(latitudes), min(longitudes), max(latitudes), max(longitudes)


def _segment_length_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    mean_lat = math.radians((a[0] + b[0]) / 2)
    north = (b[0] - a[0]) * METERS_PER_DEGREE_LATITUDE
    east = (b[1] - a[1]) * METERS_PER_DEGREE_LATITUDE * math.cos(mean_lat)
    return math.hypot(north, east)


def point_along_route(fraction: float) -> tuple[float, float]:
    """Linear interpolation by distance along `ROUTE_WAYPOINTS`; 0 is Stanton."""
    segments = list(zip(ROUTE_WAYPOINTS, ROUTE_WAYPOINTS[1:]))
    lengths = [_segment_length_m(a, b) for a, b in segments]
    remaining = min(max(fraction, 0.0), 1.0) * sum(lengths)
    for (a, b), length in zip(segments, lengths):
        if remaining <= length:
            t = remaining / length if length else 0.0
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        remaining -= length
    return ROUTE_WAYPOINTS[-1]


def offset_m(point: tuple[float, float], north: float, east: float) -> tuple[float, float]:
    lat, lon = point
    return (
        lat + north / METERS_PER_DEGREE_LATITUDE,
        lon + east / (METERS_PER_DEGREE_LATITUDE * math.cos(math.radians(lat))),
    )


def _bounding_box(index: int) -> list[float]:
    x1, y1 = 90.0 + 12 * index, 95.0
    return [x1, y1, x1 + 120.0, y1 + 70.0]


def _evidence_jpeg(class_id: int, box: list[float], caption: str) -> bytes:
    image = Image.new("RGB", EVIDENCE_SIZE, (92, 94, 98))
    draw = ImageDraw.Draw(image)
    width, height = EVIDENCE_SIZE
    draw.rectangle((0, 0, width, 60), fill=(150, 185, 215))
    for x in range(10, width, 60):
        draw.rectangle((x, 140, x + 30, 146), fill=(235, 235, 225))
    color = CLASS_COLORS.get(class_id, (200, 200, 200))
    x1, y1, x2, y2 = box
    draw.ellipse((x1 + 20, y1 + 18, x2 - 20, y2 - 12), fill=(55, 52, 50))
    draw.rectangle(box, outline=color, width=3)
    draw.rectangle((x1, y1 - 16, x1 + 7 * len(caption) + 8, y1), fill=color)
    draw.text((x1 + 4, y1 - 14), caption, fill=(255, 255, 255))
    draw.text((8, 8), "InfraScan synthetic seed", fill=(30, 40, 60))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def _placeholder_input_jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 40), (92, 94, 98)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _validate(spec: SeedIssue) -> None:
    for detection in spec.detections:
        if detection.class_id not in MODEL_CLASS_ISSUE_TYPES:
            raise ValueError(f"{spec.key}: unknown model class {detection.class_id}")
        if detection.severity not in DETECTION_SEVERITIES:
            raise ValueError(f"{spec.key}: unknown severity {detection.severity}")
    predicted = {MODEL_CLASS_ISSUE_TYPES[d.class_id] for d in spec.detections}
    if (len(predicted) > 1) != (spec.review_reason == IssueReviewReason.TYPE_CONFLICT):
        raise ValueError(f"{spec.key}: type_conflict must match mixed predictions")
    if spec.located != (spec.review_reason != IssueReviewReason.MISSING_LOCATION):
        raise ValueError(f"{spec.key}: missing_location must match unlocated detections")


def _clear_previous_seed(session: Session) -> None:
    job_ids = [seed_id("job", s.key, i) for s in SEED_ISSUES for i in range(len(s.detections))]
    issue_ids = [seed_id("issue", s.key) for s in SEED_ISSUES]
    session.execute(delete(DetectionRecord).where(DetectionRecord.job_id.in_(job_ids)))
    session.execute(delete(EvidenceMedia).where(EvidenceMedia.job_id.in_(job_ids)))
    session.execute(delete(Issue).where(Issue.issue_id.in_(issue_ids)))
    session.execute(delete(InferenceJob).where(InferenceJob.job_id.in_(job_ids)))
    session.execute(delete(InputMedia).where(InputMedia.media_id == seed_id("input-media")))


def _seed_issue(session: Session, spec: SeedIssue, input_media: InputMedia) -> int:
    _validate(spec)
    issue_point = point_along_route(spec.route_fraction)
    drive_offset = DRIVE_DURATION * spec.route_fraction
    severity_rank = {s: i for i, s in enumerate(DETECTION_SEVERITIES)}

    rows: list[tuple[DetectionRecord, InferenceJob, EvidenceMedia]] = []
    located_points: list[tuple[float, float]] = []
    for index, detection in enumerate(spec.detections):
        detected_at = FIRST_PASS_AT + PASS_INTERVAL * index + drive_offset
        location_point = offset_m(issue_point, *DETECTION_OFFSETS_M[index % len(DETECTION_OFFSETS_M)])
        # The camera captured each frame roughly 12 m before reaching the Issue.
        capture_point = point_along_route(max(spec.route_fraction - 0.002, 0.0))
        job = InferenceJob(
            job_id=seed_id("job", spec.key, index),
            input_media=input_media,
            status=InferenceJobStatus.COMPLETED.value,
            model_version=MODEL_VERSION,
            created_at=detected_at - timedelta(seconds=20),
            started_at=detected_at - timedelta(seconds=5),
            completed_at=detected_at,
            captured_at=detected_at - timedelta(minutes=1),
            latitude=Decimal(f"{capture_point[0]:.7f}"),
            longitude=Decimal(f"{capture_point[1]:.7f}"),
        )
        box = _bounding_box(index)
        evidence = EvidenceMedia(
            media_id=seed_id("evidence", spec.key, index),
            job=job,
            data=_evidence_jpeg(
                detection.class_id, box, f"{class_label(detection.class_id)} {detection.confidence:.2f}"
            ),
            content_type="image/jpeg",
            filename=f"seed-{spec.key}-{index}.jpg",
        )
        location = None
        if detection.located:
            located_points.append(location_point)
            location = {"latitude": location_point[0], "longitude": location_point[1]}
        record = DetectionRecord(
            detection_id=seed_id("detection", spec.key, index),
            job=job,
            evidence=evidence,
            issue_type=detection.class_id,
            confidence=detection.confidence,
            severity=detection.severity,
            bounding_box=box,
            location=location,
            frame_timestamp=None,
            detected_at=detected_at,
            observed_at=job.captured_at,
        )
        rows.append((record, job, evidence))

    latitude = longitude = accuracy = None
    if located_points:
        latitude = sum(p[0] for p in located_points) / len(located_points)
        longitude = sum(p[1] for p in located_points) / len(located_points)
        spread = max(_segment_length_m((latitude, longitude), p) for p in located_points)
        accuracy = round(GPS_ACCURACY_M + spread, 1)

    seen = [record.observed_at for record, _, _ in rows]
    issue = Issue(
        issue_id=seed_id("issue", spec.key),
        issue_type_code=spec.issue_type.value,
        severity=max((d.severity for d in spec.detections), key=severity_rank.__getitem__),
        status=spec.status.value,
        review_reason=spec.review_reason.value if spec.review_reason else None,
        latitude=latitude,
        longitude=longitude,
        location_accuracy_m=accuracy,
        first_seen_at=min(seen),
        last_seen_at=max(seen),
    )
    session.add(issue)
    for record, job, evidence in rows:
        record.issue = issue
        session.add_all([job, evidence, record])
    return len(rows)


def seed(session: Session) -> SeedSummary:
    """Replace the seeded rows inside the caller's transaction."""
    _clear_previous_seed(session)
    session.flush()
    input_media = InputMedia(
        media_id=seed_id("input-media"),
        data=_placeholder_input_jpeg(),
        content_type="image/jpeg",
        filename="synthetic-seed-placeholder.jpg",
    )
    session.add(input_media)
    detections = sum(_seed_issue(session, spec, input_media) for spec in SEED_ISSUES)
    session.flush()
    located = sum(spec.located for spec in SEED_ISSUES)
    return SeedSummary(
        issues=len(SEED_ISSUES),
        located_issues=located,
        unlocated_issues=len(SEED_ISSUES) - located,
        detections=detections,
    )


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    engine = create_db_engine()
    try:
        with create_session_factory(engine)() as session, session.begin():
            summary = seed(session)
    finally:
        engine.dispose()
    min_lat, min_lon, max_lat, max_lon = route_bounds()
    print(
        f"Seeded {summary.issues} Issues ({summary.located_issues} located, "
        f"{summary.unlocated_issues} without location) from {summary.detections} detections.\n"
        f"Route bounds: lat {min_lat}..{max_lat}, lon {min_lon}..{max_lon}"
    )


if __name__ == "__main__":
    main()
