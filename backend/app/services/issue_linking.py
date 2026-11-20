"""Consolidate persisted detections into canonical Issues.

Runs inside the caller's transaction: the worker's result transaction, or the
historical backfill. Detections are handled one at a time, frames in capture
order and, within a frame, most confident first.

1. Class mapping. The raw model class must map to a canonical type through
   `MODEL_CLASS_ISSUE_TYPES`. An unmapped class is saved without an Issue and
   logged; no Issue type is invented.
2. Located detections join the closest *open* Issue (`pending_review` or
   `accepted`) of the same canonical type whose point is within `radius_m`
   great-circle meters, skipping Issues that already hold a detection from
   the same image or video frame (distinct boxes in one frame are distinct
   defects, however close their projections). Ties go to the earlier
   `first_seen_at`, then the lower `issue_id`. Candidates come from a
   bounding-box query on the partial GiST index `ix_issues_location`, then
   exact distance. With no match a new Issue is created; if an open Issue of
   a *different* type lies within the radius, the new Issue is flagged
   `type_conflict`. Different types never merge.
3. Unlocated detections never match across submissions. One continues an
   unlocated Issue only within the same job, when that Issue holds a
   same-type detection from the immediately preceding sampled frame whose box
   overlaps it by IoU >= `UNLOCATED_TRACK_MIN_IOU`. Otherwise it starts a new
   unlocated Issue for review (`missing_location`).
4. `resolved` and `dismissed` Issues are never matched or modified; a new
   detection there starts a new Issue for review. `accepted` Issues gain the
   detection and wider seen times only: severity, location, status, and review
   reason are reviewer-owned.
5. New Issues start `pending_review`. While pending, an Issue's location is
   the mean of its located detections, its severity is its most severe
   detection, and its review reason is `missing_location`, else a sticky
   `type_conflict`, else `low_confidence` while no detection reaches
   `low_confidence`, else none. `location_accuracy_m` stays NULL because the
   projection is uncalibrated; no accuracy is claimed.

Callers must call `lock()` first: a transaction-scoped advisory lock that
serializes linking, so two jobs cannot both miss each other's new Issue.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    DETECTION_SEVERITIES,
    MODEL_CLASS_ISSUE_TYPES,
    DetectionRecord,
    Issue,
    IssueReviewReason,
    IssueStatus,
    issue_point,
)

logger = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_008.8
DEFAULT_MATCH_RADIUS_M = 8.0
MAX_MATCH_RADIUS_M = 100.0
DEFAULT_LOW_CONFIDENCE = 0.5
UNLOCATED_TRACK_MIN_IOU = 0.3
MATCHABLE_STATUSES = (IssueStatus.PENDING_REVIEW.value, IssueStatus.ACCEPTED.value)
# Arbitrary application-wide key for `pg_advisory_xact_lock`.
ISSUE_LINK_LOCK_KEY = 0x1D5CA915
_SEVERITY_RANK = {severity: rank for rank, severity in enumerate(DETECTION_SEVERITIES)}


def _env_float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


@dataclass(frozen=True)
class IssueMatchPolicy:
    """Tunable matching thresholds (see the module docstring)."""

    radius_m: float = DEFAULT_MATCH_RADIUS_M
    low_confidence: float = DEFAULT_LOW_CONFIDENCE

    def __post_init__(self) -> None:
        if not (math.isfinite(self.radius_m) and 0 < self.radius_m <= MAX_MATCH_RADIUS_M):
            raise ValueError(f"Issue match radius must be in (0, {MAX_MATCH_RADIUS_M}] meters")
        if not (math.isfinite(self.low_confidence) and 0 <= self.low_confidence <= 1):
            raise ValueError("Low-confidence threshold must be in [0, 1]")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> IssueMatchPolicy:
        return cls(
            radius_m=_env_float(environ, "INFRASCAN_ISSUE_MATCH_RADIUS_M", DEFAULT_MATCH_RADIUS_M),
            low_confidence=_env_float(
                environ, "INFRASCAN_ISSUE_LOW_CONFIDENCE", DEFAULT_LOW_CONFIDENCE
            ),
        )


@dataclass(frozen=True)
class FrameDetections:
    """Persisted detections from one image or sampled video frame."""

    job_id: UUID
    evidence_media_id: UUID | None
    observed_at: datetime
    records: Sequence[DetectionRecord]


@dataclass
class LinkSummary:
    linked: int = 0
    unmapped: int = 0
    created_issue_ids: set[UUID] = field(default_factory=set)
    updated_issue_ids: set[UUID] = field(default_factory=set)


@dataclass
class _IssueState:
    located: int
    max_confidence: float | None
    type_conflict: bool


@dataclass(frozen=True)
class _Track:
    issue: Issue
    issue_type: str
    bounding_box: Sequence[float]


def _wrap_degrees(delta: float) -> float:
    return (delta + 180.0) % 360.0 - 180.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def search_boxes(
    latitude: float, longitude: float, radius_m: float
) -> list[tuple[float, float, float, float]]:
    """(west, south, east, north) boxes that together cover the radius circle.

    The longitude span uses the box's poleward edge, so it is never narrower
    than the circle; a span crossing the antimeridian is split in two.
    """
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    south, north = max(latitude - dlat, -90.0), min(latitude + dlat, 90.0)
    poleward = max(abs(south), abs(north))
    if poleward >= 89.9:
        return [(-180.0, south, 180.0, north)]
    ratio = math.sin(radius_m / EARTH_RADIUS_M) / math.cos(math.radians(poleward))
    dlon = math.degrees(math.asin(min(1.0, ratio)))
    west, east = longitude - dlon, longitude + dlon
    if west < -180.0:
        return [(west + 360.0, south, 180.0, north), (-180.0, south, east, north)]
    if east > 180.0:
        return [(west, south, 180.0, north), (-180.0, south, east - 360.0, north)]
    return [(west, south, east, north)]


def candidate_issues_query(latitude: float, longitude: float, radius_m: float) -> Select:
    """Open located Issues in the boxes around a point, row-locked.

    Matches the partial GiST index `ix_issues_location`; exact distance is
    applied afterwards in Python.
    """
    point = issue_point()
    in_boxes = or_(
        *(
            point.op("<@", is_comparison=True)(
                func.box(func.point(west, south), func.point(east, north))
            )
            for west, south, east, north in search_boxes(latitude, longitude, radius_m)
        )
    )
    return (
        select(Issue)
        .where(
            Issue.latitude.is_not(None),
            Issue.longitude.is_not(None),
            in_boxes,
            Issue.status.in_(MATCHABLE_STATUSES),
        )
        .order_by(Issue.issue_id)
        .with_for_update()
    )


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    width = min(ax2, bx2) - max(ax1, bx1)
    height = min(ay2, by2) - max(ay1, by1)
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection
    return intersection / union if union > 0 else 0.0


class IssueLinker:
    """Assigns detections to Issues within the caller's open transaction."""

    def __init__(self, session: Session, policy: IssueMatchPolicy) -> None:
        self._session = session
        self._policy = policy
        self._states: dict[UUID, _IssueState] = {}

    def lock(self) -> None:
        """Serialize Issue matching until the current transaction ends."""
        self._session.execute(select(func.pg_advisory_xact_lock(ISSUE_LINK_LOCK_KEY)))

    def link(self, frames: Sequence[FrameDetections]) -> LinkSummary:
        """Link `frames` (one job's frames contiguous, in capture order)."""
        summary = LinkSummary()
        tracks: list[_Track] = []
        previous_job: UUID | None = None
        for frame in frames:
            if frame.job_id != previous_job:
                tracks = []
            previous_job = frame.job_id
            used = self._issues_in_frame(frame)
            next_tracks: list[_Track] = []
            ordered = sorted(
                enumerate(frame.records), key=lambda item: (-item[1].confidence, item[0])
            )
            for _, record in ordered:
                issue_type = MODEL_CLASS_ISSUE_TYPES.get(record.issue_type)
                if issue_type is None:
                    summary.unmapped += 1
                    logger.warning(
                        "Detection %s in job %s has unmapped model class %s; "
                        "it is saved without an Issue",
                        record.detection_id, frame.job_id, record.issue_type,
                    )
                    continue
                if record.location is not None:
                    issue, created = self._link_located(
                        record, issue_type.value, frame.observed_at, used
                    )
                else:
                    issue, created = self._link_unlocated(
                        record, issue_type.value, frame.observed_at, used, tracks
                    )
                    next_tracks.append(_Track(issue, issue_type.value, record.bounding_box))
                used.add(issue.issue_id)
                summary.linked += 1
                if created:
                    summary.created_issue_ids.add(issue.issue_id)
                elif issue.issue_id not in summary.created_issue_ids:
                    summary.updated_issue_ids.add(issue.issue_id)
            tracks = next_tracks
        self._session.flush()
        return summary

    def _issues_in_frame(self, frame: FrameDetections) -> set[UUID]:
        clauses = [DetectionRecord.issue_id.is_not(None)]
        if frame.evidence_media_id is not None:
            clauses.append(DetectionRecord.evidence_media == frame.evidence_media_id)
        else:
            clauses += [
                DetectionRecord.job_id == frame.job_id,
                DetectionRecord.evidence_media.is_(None),
            ]
        return set(self._session.scalars(select(DetectionRecord.issue_id).where(*clauses)))

    def _nearby_open_issues(self, latitude: float, longitude: float) -> list[tuple[float, Issue]]:
        issues = self._session.scalars(
            candidate_issues_query(latitude, longitude, self._policy.radius_m)
        ).all()
        nearby = []
        for issue in issues:
            distance = haversine_m(latitude, longitude, issue.latitude, issue.longitude)
            if distance <= self._policy.radius_m:
                nearby.append((distance, issue))
        return nearby

    def _link_located(
        self,
        record: DetectionRecord,
        issue_type: str,
        observed_at: datetime,
        used: set[UUID],
    ) -> tuple[Issue, bool]:
        latitude = float(record.location["latitude"])
        longitude = float(record.location["longitude"])
        nearby = self._nearby_open_issues(latitude, longitude)
        same_type = [
            (distance, issue.first_seen_at, issue.issue_id, issue)
            for distance, issue in nearby
            if issue.issue_type_code == issue_type and issue.issue_id not in used
        ]
        if same_type:
            issue = min(same_type, key=lambda candidate: candidate[:3])[3]
            self._attach(issue, record, observed_at)
            return issue, False
        conflict = any(issue.issue_type_code != issue_type for _, issue in nearby)
        return self._create(issue_type, record, observed_at, conflict), True

    def _link_unlocated(
        self,
        record: DetectionRecord,
        issue_type: str,
        observed_at: datetime,
        used: set[UUID],
        tracks: Sequence[_Track],
    ) -> tuple[Issue, bool]:
        candidates = []
        for index, track in enumerate(tracks):
            if track.issue_type != issue_type or track.issue.issue_id in used:
                continue
            overlap = box_iou(track.bounding_box, record.bounding_box)
            if overlap >= UNLOCATED_TRACK_MIN_IOU:
                candidates.append((overlap, -index, track.issue))
        if candidates:
            issue = max(candidates, key=lambda candidate: candidate[:2])[2]
            self._attach(issue, record, observed_at)
            return issue, False
        return self._create(issue_type, record, observed_at, conflict=False), True

    def _create(
        self,
        issue_type: str,
        record: DetectionRecord,
        observed_at: datetime,
        conflict: bool,
    ) -> Issue:
        # Location, severity, and review reason are filled in by `_attach`.
        issue = Issue(
            issue_id=uuid4(),
            issue_type_code=issue_type,
            severity=record.severity,
            status=IssueStatus.PENDING_REVIEW.value,
            review_reason=None,
            latitude=None,
            longitude=None,
            location_accuracy_m=None,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )
        self._session.add(issue)
        self._states[issue.issue_id] = _IssueState(0, None, conflict)
        self._attach(issue, record, observed_at)
        return issue

    def _state(self, issue: Issue) -> _IssueState:
        state = self._states.get(issue.issue_id)
        if state is None:
            located, max_confidence = self._session.execute(
                select(
                    func.count(DetectionRecord.location),
                    func.max(DetectionRecord.confidence),
                ).where(DetectionRecord.issue_id == issue.issue_id)
            ).one()
            state = _IssueState(
                located,
                max_confidence,
                issue.review_reason == IssueReviewReason.TYPE_CONFLICT.value,
            )
            self._states[issue.issue_id] = state
        return state

    def _attach(self, issue: Issue, record: DetectionRecord, observed_at: datetime) -> None:
        # Read before linking so the record is not counted twice.
        state = self._state(issue)
        record.issue_id = issue.issue_id
        issue.first_seen_at = min(issue.first_seen_at, observed_at)
        issue.last_seen_at = max(issue.last_seen_at, observed_at)
        if issue.status != IssueStatus.PENDING_REVIEW.value:
            return

        if record.location is not None:
            latitude = float(record.location["latitude"])
            longitude = float(record.location["longitude"])
            # A stored location without located detections counts as one point.
            weight = state.located or (1 if issue.latitude is not None else 0)
            if weight == 0:
                issue.latitude, issue.longitude = latitude, longitude
            else:
                issue.latitude += (latitude - issue.latitude) / (weight + 1)
                issue.longitude = _wrap_degrees(
                    issue.longitude
                    + _wrap_degrees(longitude - issue.longitude) / (weight + 1)
                )
            state.located += 1
        if _SEVERITY_RANK[record.severity] > _SEVERITY_RANK.get(issue.severity, -1):
            issue.severity = record.severity
        if state.max_confidence is None or record.confidence > state.max_confidence:
            state.max_confidence = record.confidence
        issue.review_reason = self._review_reason(issue, state)

    def _review_reason(self, issue: Issue, state: _IssueState) -> str | None:
        if issue.latitude is None:
            return IssueReviewReason.MISSING_LOCATION.value
        if state.type_conflict:
            return IssueReviewReason.TYPE_CONFLICT.value
        if state.max_confidence is None or state.max_confidence < self._policy.low_confidence:
            return IssueReviewReason.LOW_CONFIDENCE.value
        return None
