import math
from datetime import UTC, datetime
from typing import Annotated, Generic, Literal, Self, TypeVar
from uuid import UUID

from pydantic import (
    AwareDatetime,
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.models.detection_record import DetectionSeverity
from app.models.issue import IssueStatus, IssueTypeCode

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


MIN_CAMERA_UTC_OFFSET_MINUTES = -720
MAX_CAMERA_UTC_OFFSET_MINUTES = 840


class VideoSubmissionMetadata(BaseModel):
    """Client metadata for an A119 Mini 2 MP4; time and place come from its GPS."""

    model_config = ConfigDict(extra="forbid")

    camera_utc_offset_minutes: int = Field(
        ge=MIN_CAMERA_UTC_OFFSET_MINUTES,
        le=MAX_CAMERA_UTC_OFFSET_MINUTES,
        description=(
            "The dashcam's configured time-zone offset from UTC in minutes, "
            "for example -480 for UTC-08:00. Must be a multiple of 15."
        ),
    )

    @field_validator("camera_utc_offset_minutes", mode="before")
    @classmethod
    def validate_offset(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("camera_utc_offset_minutes must be a JSON integer")
        if value % 15:
            raise ValueError("camera_utc_offset_minutes must be a multiple of 15")
        return value


class ResolvedVideoSubmission(InferenceSubmissionMetadata):
    """Server-derived job metadata for a validated MP4: the first valid GPS fix."""

    camera_utc_offset_minutes: int


class InferenceJobAccepted(BaseModel):
    job_id: UUID
    status: Literal["pending"]
    submitted_at: Annotated[AwareDatetime, AfterValidator(_normalize_utc)]


DirectUploadContentType = Literal["image/jpeg", "video/mp4"]


class DirectUploadRequest(BaseModel):
    """Ask for a signed URL to upload one JPEG or MP4 straight to object storage."""

    model_config = ConfigDict(extra="forbid")

    content_type: DirectUploadContentType
    size_bytes: int = Field(gt=0, description="Exact size of the file that will be uploaded.")


class DirectUploadTicket(BaseModel):
    upload_id: UUID
    upload_url: str = Field(description="PUT the file here, with exactly `headers`.")
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_in_seconds: int


class DirectJobSubmission(BaseModel):
    """Start inference on a file already uploaded with a `DirectUploadTicket`.

    `metadata` follows the same contract as the multipart `metadata` part.
    """

    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    content_type: DirectUploadContentType
    filename: str | None = Field(default=None, max_length=255)
    metadata: dict[str, object]


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
    """A reviewer's decision on an Issue. Omitted fields stay unchanged."""

    model_config = ConfigDict(extra="forbid")

    status: IssueStatus | None = Field(
        default=None,
        description=(
            "accepted (a real issue), dismissed (not a real issue), resolved "
            "(repaired), or pending_review to reopen it."
        ),
    )
    severity: DetectionSeverity | None = None
    review_note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_a_change(self) -> Self:
        if self.status is None and self.severity is None and self.review_note is None:
            raise ValueError("Set at least one of status, severity or review_note")
        return self


Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class BoundingBox(BaseModel):
    """Inclusive viewport; boxes crossing the antimeridian are not supported."""

    min_latitude: Latitude
    min_longitude: Longitude
    max_latitude: Latitude
    max_longitude: Longitude

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.min_latitude > self.max_latitude:
            raise ValueError("min_latitude must be less than or equal to max_latitude")
        if self.min_longitude > self.max_longitude:
            raise ValueError(
                "min_longitude must be less than or equal to max_longitude; "
                "bounding boxes crossing the antimeridian are not supported"
            )
        return self

    def as_geojson_bbox(self) -> tuple[float, float, float, float]:
        """RFC 7946 order: west, south, east, north."""
        return (
            self.min_longitude,
            self.min_latitude,
            self.max_longitude,
            self.max_latitude,
        )


class IssueFilters(BaseModel):
    """Server-side Issue filters; repeat a list parameter to match any value."""

    issue_type: list[IssueTypeCode] = Field(default_factory=list)
    severity: list[DetectionSeverity] = Field(default_factory=list)
    status: list[IssueStatus] = Field(default_factory=list)
    review_required: bool = False
    last_seen_after: AwareDatetime | None = None
    last_seen_before: AwareDatetime | None = None
    job_id: UUID | None = Field(
        default=None,
        description="Only Issues supported by at least one detection from this inference job.",
    )

    @model_validator(mode="after")
    def validate_last_seen_range(self) -> Self:
        if (
            self.last_seen_after is not None
            and self.last_seen_before is not None
            and self.last_seen_after > self.last_seen_before
        ):
            raise ValueError("last_seen_after must not be later than last_seen_before")
        return self


ISSUE_MAP_DEFAULT_LIMIT = 500
ISSUE_MAP_MAX_LIMIT = 1000


class IssueMapQuery(BoundingBox, IssueFilters):
    limit: int = Field(default=ISSUE_MAP_DEFAULT_LIMIT, ge=1, le=ISSUE_MAP_MAX_LIMIT)

    def filters(self) -> IssueFilters:
        return IssueFilters.model_validate(
            self.model_dump(include=set(IssueFilters.model_fields))
        )

    def bounding_box(self) -> BoundingBox:
        return BoundingBox.model_validate(
            self.model_dump(include=set(BoundingBox.model_fields))
        )


class IssueListQuery(IssueFilters):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    min_latitude: Latitude | None = None
    min_longitude: Longitude | None = None
    max_latitude: Latitude | None = None
    max_longitude: Longitude | None = None

    @model_validator(mode="after")
    def validate_bounding_box(self) -> Self:
        self.bounding_box()
        return self

    def filters(self) -> IssueFilters:
        return IssueFilters.model_validate(
            self.model_dump(include=set(IssueFilters.model_fields))
        )

    def bounding_box(self) -> BoundingBox | None:
        values = self.model_dump(include=set(BoundingBox.model_fields))
        provided = [v for v in values.values() if v is not None]
        if not provided:
            return None
        if len(provided) != len(values):
            raise ValueError(
                "min_latitude, min_longitude, max_latitude, and max_longitude "
                "must be provided together"
            )
        return BoundingBox.model_validate(values)


class IssueSummaryFields(BaseModel):
    issue_id: UUID
    issue_type_code: str
    issue_type_name: str
    severity: str
    status: str
    review_reason: str | None = None
    needs_review: bool
    location_accuracy_m: float | None = Field(default=None, ge=0)
    first_seen_at: datetime
    last_seen_at: datetime
    detection_count: int = Field(ge=0)
    evidence_thumbnail_url: str | None = Field(
        default=None,
        description="Path relative to the API origin for the representative evidence image.",
    )


class IssueDTO(IssueSummaryFields):
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    created_at: datetime
    updated_at: datetime
    review_note: str | None = None
    reviewed_at: datetime | None = None


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[Longitude, Latitude] = Field(
        description="[longitude, latitude] per RFC 7946."
    )


class IssueFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: UUID
    geometry: PointGeometry
    properties: IssueSummaryFields


class IssueMapMeta(BaseModel):
    limit: int = Field(ge=1)
    returned: int = Field(ge=0)
    total: int = Field(ge=0, description="Matching located Issues in the viewport.")
    truncated: bool


class IssueFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    bbox: tuple[Longitude, Latitude, Longitude, Latitude]
    features: list[IssueFeature]
    meta: IssueMapMeta


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
    detected_at: datetime = Field(description="When inference finished.")
    observed_at: datetime | None = Field(
        default=None,
        description=(
            "When the source image or video frame was captured (UTC); NULL only for "
            "historical video detections whose frame time is unknown."
        ),
    )
    issue_id: UUID | None = Field(
        default=None,
        description=(
            "The canonical Issue this detection supports; NULL for an unmapped "
            "model class or a historical row that has not been backfilled."
        ),
    )
    issue_status: str | None = Field(
        default=None,
        description="Review status of the Issue this detection supports, when it has one.",
    )
    # Raw v0-mvp model class ID as predicted, not a canonical issue type.
    issue_type: int | None = None
    predicted_issue_type_code: str | None = None
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


class CurrentUserDTO(BaseModel):
    id: UUID = Field(description="Supabase Auth user ID (the access token's `sub`).")
    role: str
    municipality_id: str | None = None


class APIError(BaseModel):
    code: str
    message: str
    field: str | None = None


class APIErrorResponse(BaseModel):
    error: APIError
