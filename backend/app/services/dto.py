from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.models import MODEL_CLASS_ISSUE_TYPES, DetectionRecord, IssueStatus
from app.repositories import IssueRow
from app.schemas import DetectionDTO, DetectionLocation, IssueDTO, IssueSummaryFields
from inference.labels import class_label

EVIDENCE_PATH_PREFIX = "/api/v1/evidence"


def utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


def evidence_path(media_id: UUID | None) -> str | None:
    return f"{EVIDENCE_PATH_PREFIX}/{media_id}" if media_id is not None else None


def detection_to_dto(record: DetectionRecord) -> DetectionDTO:
    location = (
        DetectionLocation.model_validate(record.location)
        if record.location is not None
        else None
    )
    predicted_type = MODEL_CLASS_ISSUE_TYPES.get(record.issue_type)
    return DetectionDTO(
        detection_id=record.detection_id,
        job_id=record.job_id,
        classification=class_label(record.issue_type),
        severity=record.severity,
        confidence=record.confidence,
        latitude=location.latitude if location else None,
        longitude=location.longitude if location else None,
        detected_at=utc(record.detected_at),
        issue_id=record.issue_id,
        issue_type=record.issue_type,
        predicted_issue_type_code=predicted_type.value if predicted_type else None,
        evidence_media=record.evidence_media,
        location=location,
        bounding_box=list(record.bounding_box),
        frame_timestamp=record.frame_timestamp,
    )


def issue_summary(row: IssueRow) -> IssueSummaryFields:
    issue = row.issue
    return IssueSummaryFields(
        issue_id=issue.issue_id,
        issue_type_code=issue.issue_type_code,
        issue_type_name=row.issue_type_name,
        severity=issue.severity,
        status=issue.status,
        review_reason=issue.review_reason,
        needs_review=(
            issue.status == IssueStatus.PENDING_REVIEW or issue.review_reason is not None
        ),
        location_accuracy_m=issue.location_accuracy_m,
        first_seen_at=utc(issue.first_seen_at),
        last_seen_at=utc(issue.last_seen_at),
        detection_count=row.detection_count,
        evidence_thumbnail_url=evidence_path(row.evidence_media_id),
    )


def issue_to_dto(row: IssueRow) -> IssueDTO:
    return IssueDTO(
        **issue_summary(row).model_dump(),
        latitude=row.issue.latitude,
        longitude=row.issue.longitude,
        created_at=utc(row.issue.created_at),
        updated_at=utc(row.issue.updated_at),
    )
