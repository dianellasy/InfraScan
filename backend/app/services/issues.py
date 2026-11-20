from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.repositories import IssueRepository
from app.schemas import (
    BoundingBox,
    DetectionDTO,
    IssueDTO,
    IssueFeature,
    IssueFeatureCollection,
    IssueFilters,
    IssueMapMeta,
    Page,
    PointGeometry,
)
from app.services.dto import detection_to_dto, issue_summary, issue_to_dto


@dataclass(frozen=True)
class EvidenceImage:
    data: bytes
    content_type: str


class IssueQueries:
    """Blocking Issue reads; callers run these in a thread pool."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def map_features(
        self, bbox: BoundingBox, filters: IssueFilters, limit: int
    ) -> IssueFeatureCollection:
        with self._session_factory() as session:
            rows, total = IssueRepository(session).list_in_bbox(bbox, filters, limit)
        features = [
            IssueFeature(
                id=row.issue.issue_id,
                geometry=PointGeometry(
                    coordinates=(row.issue.longitude, row.issue.latitude)
                ),
                properties=issue_summary(row),
            )
            for row in rows
        ]
        return IssueFeatureCollection(
            bbox=bbox.as_geojson_bbox(),
            features=features,
            meta=IssueMapMeta(
                limit=limit,
                returned=len(features),
                total=total,
                truncated=total > len(features),
            ),
        )

    def list_issues(
        self,
        filters: IssueFilters,
        bbox: BoundingBox | None,
        page: int,
        page_size: int,
    ) -> Page[IssueDTO]:
        with self._session_factory() as session:
            rows, total = IssueRepository(session).list_filtered(
                filters, bbox, offset=(page - 1) * page_size, limit=page_size
            )
        return Page[IssueDTO](
            items=[issue_to_dto(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def review_queue(self, page: int, page_size: int) -> Page[IssueDTO]:
        with self._session_factory() as session:
            rows, total = IssueRepository(session).list_review_queue(
                offset=(page - 1) * page_size, limit=page_size
            )
        return Page[IssueDTO](
            items=[issue_to_dto(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get(self, issue_id: UUID) -> IssueDTO | None:
        with self._session_factory() as session:
            row = IssueRepository(session).get(issue_id)
        return issue_to_dto(row) if row is not None else None

    def detections(
        self, issue_id: UUID, page: int, page_size: int
    ) -> Page[DetectionDTO] | None:
        with self._session_factory() as session:
            issues = IssueRepository(session)
            if not issues.exists(issue_id):
                return None
            records, total = issues.list_detections(
                issue_id, offset=(page - 1) * page_size, limit=page_size
            )
            return Page[DetectionDTO](
                items=[detection_to_dto(record) for record in records],
                total=total,
                page=page,
                page_size=page_size,
            )

    def evidence(self, media_id: UUID) -> EvidenceImage | None:
        with self._session_factory() as session:
            media = IssueRepository(session).get_evidence(media_id)
            if media is None:
                return None
            return EvidenceImage(media.data, media.content_type)
