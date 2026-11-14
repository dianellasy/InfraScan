import math
from datetime import UTC, datetime
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    AwareDatetime,
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)

T = TypeVar("T")


def _normalize_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class InferenceMetadata(BaseModel):
    captured_at: datetime | None = None
    source_id: str | None = Field(default=None, max_length=255)
    road_id: UUID | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class InferenceSubmissionMetadata(BaseModel):
    """Validated client metadata for a single-image inference submission."""

    model_config = ConfigDict(extra="forbid")

    captured_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("captured_at", mode="before")
    @classmethod
    def validate_captured_at(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("captured_at must be an ISO 8601 datetime string")

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("captured_at must be a valid ISO 8601 datetime") from exc

        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return parsed.astimezone(UTC)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def validate_coordinate(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("coordinate must be a JSON number")
        if not math.isfinite(value):
            raise ValueError("coordinate must be finite")
        return float(value)


class InferenceJobAccepted(BaseModel):
    job_id: UUID
    status: Literal["pending"]
    submitted_at: Annotated[AwareDatetime, AfterValidator(_normalize_utc)]


class InferenceJobDTO(InferenceJobAccepted):
    status: str
    submitted_at: datetime
    progress: int | None = Field(default=None, ge=0, le=100)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metadata: InferenceMetadata | None = None
    model_version: str | None = None


class IssueCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    severity: str
    road_id: UUID | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class IssueUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = None
    severity: str | None = None


class IssueDTO(IssueCreate):
    issue_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime


class RoadDTO(BaseModel):
    road_id: UUID
    name: str | None = None
    geometry: dict[str, object]
    metadata: dict[str, object] = Field(default_factory=dict)


class DetectionLocation(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DetectionDTO(BaseModel):
    detection_id: UUID
    job_id: UUID
    road_id: UUID | None = None
    classification: str
    severity: str
    confidence: float = Field(ge=0, le=1)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    detected_at: datetime
    issue_id: UUID | None = None
    # Provisional: the v0-mvp model class ID, not a permanent issue-type ID.
    issue_type: int | None = None
    evidence_media: UUID | None = None
    location: DetectionLocation | None = None
    bounding_box: (
        Annotated[list[float], Field(min_length=4, max_length=4)] | None
    ) = Field(
        default=None,
        description=(
            "[x1, y1, x2, y2] in pixels of the original prediction source image."
        ),
    )
    frame_timestamp: float | None = Field(default=None, ge=0)


class EvidenceFrameDTO(BaseModel):
    timestamp_seconds: float | None = Field(default=None, ge=0)
    original_url: HttpUrl
    annotated_url: HttpUrl | None = None
    bounding_box: dict[str, float] | None = None


class DetectionEvidenceDTO(BaseModel):
    detection_id: UUID
    frames: list[EvidenceFrameDTO]


class AnalyticsOverviewDTO(BaseModel):
    surveyed_road_segments: int = Field(ge=0)
    unique_detections: int = Field(ge=0)
    severity_distribution: dict[str, int]


class RoadConditionDTO(BaseModel):
    road_id: UUID
    detection_count: int = Field(ge=0)
    severity_distribution: dict[str, int]
    damage_concentration: float | None = Field(default=None, ge=0)


class GeographicAggregateDTO(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    detection_count: int = Field(ge=0)
    classification: str | None = None
    severity: str | None = None


class DamageDistributionDTO(BaseModel):
    classification: str
    severity: str
    detection_count: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)


class APIError(BaseModel):
    code: str
    message: str
    field: str | None = None


class APIErrorResponse(BaseModel):
    error: APIError
