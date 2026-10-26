from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_service
from app.schemas import DetectionDTO, InferenceJobDTO, Page
from app.services.contracts import InfraScanService

router = APIRouter(prefix="/jobs", tags=["inference jobs"])


@router.get("", response_model=Page[InferenceJobDTO])
async def list_jobs(
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    submitted_after: datetime | None = None,
    submitted_before: datetime | None = None,
) -> Page[InferenceJobDTO]:
    return await service.list_jobs(
        page, page_size, status_filter, submitted_after, submitted_before
    )


@router.get("/{job_id}", response_model=InferenceJobDTO)
async def get_job(
    job_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> InferenceJobDTO:
    return await service.get_job(job_id)


@router.get("/{job_id}/detections", response_model=Page[DetectionDTO])
async def list_job_detections(
    job_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[DetectionDTO]:
    return await service.list_job_detections(job_id, page, page_size)
