from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_service
from app.api.errors import error_response
from app.api.media_urls import RequestMediaUrls
from app.schemas import APIErrorResponse, DetectionDTO, InferenceJobDTO, Page
from app.services.contracts import InfraScanService, JobNotFoundError

router = APIRouter(prefix="/jobs", tags=["inference jobs"])

_JOB_NOT_FOUND_RESPONSES = {
    404: {"model": APIErrorResponse, "description": "Unknown inference job"},
}


def _job_not_found() -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        "JOB_NOT_FOUND",
        "The inference job was not found.",
        "job_id",
    )


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


@router.get(
    "/{job_id}",
    response_model=InferenceJobDTO,
    responses=_JOB_NOT_FOUND_RESPONSES,
)
async def get_job(
    job_id: UUID,
    request: Request,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> InferenceJobDTO | JSONResponse:
    try:
        return await service.get_job(job_id, RequestMediaUrls(request))
    except JobNotFoundError:
        return _job_not_found()


@router.get(
    "/{job_id}/detections",
    response_model=Page[DetectionDTO],
    responses=_JOB_NOT_FOUND_RESPONSES,
)
async def list_job_detections(
    job_id: UUID,
    request: Request,
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[DetectionDTO] | JSONResponse:
    try:
        return await service.list_job_detections(
            job_id, page, page_size, RequestMediaUrls(request)
        )
    except JobNotFoundError:
        return _job_not_found()
