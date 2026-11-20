from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, Protocol, Sequence
from uuid import UUID

from app.schemas import (
    AnalyticsOverviewDTO,
    DamageDistributionDTO,
    DetectionDTO,
    DetectionEvidenceDTO,
    GeographicAggregateDTO,
    InferenceSubmissionMetadata,
    IssueCreate,
    IssueDTO,
    Page,
    RoadConditionDTO,
    RoadDTO,
)

if TYPE_CHECKING:
    from app.models import InferenceJob



class StoredMedia(Protocol):
    media_id: UUID
    content_type: str
    filename: str | None


class MediaStorageAdapter(Protocol):
    def put_file(self, source: BinaryIO, key: str, content_type: str) -> None: ...
    def download_to(self, key: str, target: BinaryIO, max_bytes: int) -> None: ...

    def put(
      self, data: bytes, object_key: str, content_type: str
    ) -> None:
        """Store the image to R2 location using object_key """
        ...
    def get(self, key: str) -> bytes:
        """Using the key, find and return the image"""
        ...

    def delete(self, key: str) -> None:
        """Using the key, find and delete the image"""
        ...

class InferenceJobRepositoryAdapter(Protocol):
    def create_pending(
        self, media: StoredMedia, metadata: InferenceSubmissionMetadata
    ) -> InferenceJob: ...

    def get(self, job_id: UUID) -> InferenceJob | None: ...


class InferenceQueueAdapter(Protocol):
    def enqueue(self, job_id: UUID) -> None:
        """Publish only the application job ID; workers load everything else."""
        ...


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
