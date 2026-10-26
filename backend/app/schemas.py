from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

T = TypeVar("T")


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


class InferenceJobAccepted(BaseModel):
    job_id: UUID
    status: str
    submitted_at: datetime


class InferenceJobDTO(InferenceJobAccepted):
    progress: int | None = Field(default=None, ge=0, le=100)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    metadata: InferenceMetadata | None = None


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
