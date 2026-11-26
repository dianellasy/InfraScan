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
    """Object storage for media payloads. Keys never start with "/"."""

    def put(self, data: bytes, object_key: str, content_type: str) -> None:
        """Store a small, already-bounded payload such as a JPEG."""
        ...

    def put_file(self, source: BinaryIO, object_key: str, content_type: str) -> None:
        """Stream a seekable file to storage from its start (multipart when large)."""
        ...

    def get(self, key: str) -> bytes:
        """Return a small object such as a JPEG; implementations cap the size."""
        ...

    def download_to_file(self, key: str, target: BinaryIO, max_bytes: int) -> int:
        """Stream an object into `target`; fail before writing past `max_bytes`."""
        ...

    def delete(self, key: str) -> None:
        """Delete an object; deleting a missing key is not an error."""
        ...


class DirectUploadStorageAdapter(MediaStorageAdapter, Protocol):
    """Storage the browser can upload to directly with a short-lived signed URL."""

    def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> str:
        """A URL accepting one PUT of exactly `content_length` bytes of `content_type`."""
        ...

    def size(self, key: str) -> int | None:
        """Size in bytes of a stored object, or None if it does not exist."""
        ...

class InferenceJobRepositoryAdapter(Protocol):
    def create_pending(
        self, media: StoredMedia, metadata: InferenceSubmissionMetadata, user_id: UUID
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
