from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_service
from app.schemas import DetectionDTO, DetectionEvidenceDTO, Page
from app.services.contracts import InfraScanService

router = APIRouter(prefix="/detections", tags=["detections"])


@router.get("", response_model=Page[DetectionDTO])
async def list_detections(
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    classification: str | None = None,
    severity: str | None = None,
    confidence_min: Annotated[float | None, Query(ge=0, le=1)] = None,
    confidence_max: Annotated[float | None, Query(ge=0, le=1)] = None,
    detected_after: datetime | None = None,
    detected_before: datetime | None = None,
    min_latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    min_longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    max_latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    max_longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> Page[DetectionDTO]:
    return await service.list_detections(
        page,
        page_size,
        classification,
        severity,
        confidence_min,
        confidence_max,
        detected_after,
        detected_before,
        min_latitude,
        min_longitude,
        max_latitude,
        max_longitude,
    )


@router.get("/{detection_id}", response_model=DetectionDTO)
async def get_detection(
    detection_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> DetectionDTO:
    return await service.get_detection(detection_id)


@router.get("/{detection_id}/evidence", response_model=DetectionEvidenceDTO)
async def get_detection_evidence(
    detection_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> DetectionEvidenceDTO:
    return await service.get_detection_evidence(detection_id)
