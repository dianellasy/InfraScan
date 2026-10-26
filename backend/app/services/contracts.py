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
    InferenceJobAccepted,
    InferenceJobDTO,
    InferenceMetadata,
    IssueCreate,
    IssueDTO,
    IssueUpdate,
    Page,
    RoadConditionDTO,
    RoadDTO,
)


class InfraScanService(Protocol):
    async def submit_inference(
        self, files: Sequence[UploadFile], metadata: InferenceMetadata
    ) -> InferenceJobAccepted: ...

    async def list_issues(
        self, page: int, page_size: int, status: str | None
    ) -> Page[IssueDTO]: ...

    async def get_issue(self, issue_id: UUID) -> IssueDTO: ...

    async def create_issue(self, issue: IssueCreate) -> IssueDTO: ...

    async def update_issue(self, issue_id: UUID, update: IssueUpdate) -> IssueDTO: ...

    async def list_review_queue(
        self, page: int, page_size: int
    ) -> Page[IssueDTO]: ...

    async def list_jobs(
        self,
        page: int,
        page_size: int,
        status: str | None,
        submitted_after: datetime | None,
        submitted_before: datetime | None,
    ) -> Page[InferenceJobDTO]: ...

    async def get_job(self, job_id: UUID) -> InferenceJobDTO: ...

    async def list_job_detections(
        self, job_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO]: ...

    async def list_roads(
        self,
        page: int,
        page_size: int,
        min_latitude: float | None,
        min_longitude: float | None,
        max_latitude: float | None,
        max_longitude: float | None,
    ) -> Page[RoadDTO]: ...

    async def get_road(self, road_id: UUID) -> RoadDTO: ...

    async def list_road_issues(
        self, road_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO]: ...

    async def list_detections(
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

    async def get_detection(self, detection_id: UUID) -> DetectionDTO: ...

    async def get_detection_evidence(
        self, detection_id: UUID
    ) -> DetectionEvidenceDTO: ...

    async def get_analytics_overview(
        self, from_date: datetime | None, to_date: datetime | None
    ) -> AnalyticsOverviewDTO: ...

    async def get_road_conditions(
        self, from_date: datetime | None, to_date: datetime | None, severity: str | None
    ) -> list[RoadConditionDTO]: ...

    async def get_geographic_analytics(
        self,
        from_date: datetime | None,
        to_date: datetime | None,
        classification: str | None,
        severity: str | None,
        min_latitude: float | None,
        min_longitude: float | None,
        max_latitude: float | None,
        max_longitude: float | None,
    ) -> list[GeographicAggregateDTO]: ...

    async def get_damage_distribution(
        self, from_date: datetime | None, to_date: datetime | None
    ) -> list[DamageDistributionDTO]: ...
