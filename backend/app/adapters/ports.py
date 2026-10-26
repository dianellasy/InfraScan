from datetime import datetime
from typing import Protocol, Sequence
from uuid import UUID

from fastapi import UploadFile

from app.schemas import (
    AnalyticsOverviewDTO,
    DamageDistributionDTO,
    DetectionDTO,
    DetectionEvidenceDTO,
    GeographicAggregateDTO,
    InferenceJobDTO,
    InferenceMetadata,
    IssueCreate,
    IssueDTO,
    Page,
    RoadConditionDTO,
    RoadDTO,
)


class StoredMedia(Protocol):
    object_key: str
    content_type: str
    filename: str


class MediaStorageAdapter(Protocol):
    async def store(self, files: Sequence[UploadFile]) -> Sequence[StoredMedia]: ...


class InferenceJobRepositoryAdapter(Protocol):
    async def create(
        self, metadata: InferenceMetadata, media: Sequence[StoredMedia]
    ) -> InferenceJobDTO: ...

    async def get(self, job_id: UUID) -> InferenceJobDTO | None: ...

    async def list(
        self,
        page: int,
        page_size: int,
        status: str | None,
        submitted_after: datetime | None,
        submitted_before: datetime | None,
    ) -> Page[InferenceJobDTO]: ...

    async def list_detections(
        self, job_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO]: ...


class InferenceQueueAdapter(Protocol):
    async def enqueue(self, job_id: UUID, media: Sequence[StoredMedia]) -> None: ...


class IssueRepositoryAdapter(Protocol):
    async def list(
        self, page: int, page_size: int, status: str | None
    ) -> Page[IssueDTO]: ...

    async def get(self, issue_id: UUID) -> IssueDTO | None: ...

    async def create(self, issue: IssueCreate) -> IssueDTO: ...

    async def update(self, issue_id: UUID, changes: dict[str, object]) -> IssueDTO | None: ...

    async def list_review_queue(self, page: int, page_size: int) -> Page[IssueDTO]: ...

class RoadRepositoryAdapter(Protocol):
    async def list(
        self,
        page: int,
        page_size: int,
        min_latitude: float | None,
        min_longitude: float | None,
        max_latitude: float | None,
        max_longitude: float | None,
    ) -> Page[RoadDTO]: ...

    async def get(self, road_id: UUID) -> RoadDTO | None: ...


class DetectionRepositoryAdapter(Protocol):
    async def list(
        self,
        page: int,
        page_size: int,
        classification: str | None,
        severity: str | None,
        confidence_min: float | None,
        confidence_max: float | None,
        detected_after: datetime | None,
        detected_before: datetime | None,
        min_latitude: float | None,
        min_longitude: float | None,
        max_latitude: float | None,
        max_longitude: float | None,
    ) -> Page[DetectionDTO]: ...

    async def list_for_road(
        self, road_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO]: ...

    async def get(self, detection_id: UUID) -> DetectionDTO | None: ...

    async def get_evidence(
        self, detection_id: UUID
    ) -> DetectionEvidenceDTO | None: ...


class AnalyticsRepositoryAdapter(Protocol):
    async def overview(
        self, from_date: datetime | None, to_date: datetime | None
    ) -> AnalyticsOverviewDTO: ...

    async def road_conditions(
        self, from_date: datetime | None, to_date: datetime | None, severity: str | None
    ) -> Sequence[RoadConditionDTO]: ...

    async def geographic(
        self,
        from_date: datetime | None,
        to_date: datetime | None,
        classification: str | None,
        severity: str | None,
        min_latitude: float | None,
        min_longitude: float | None,
        max_latitude: float | None,
        max_longitude: float | None,
    ) -> Sequence[GeographicAggregateDTO]: ...

    async def damage_distribution(
        self, from_date: datetime | None, to_date: datetime | None
    ) -> Sequence[DamageDistributionDTO]: ...
