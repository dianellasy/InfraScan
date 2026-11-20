from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from app.api.dependencies import get_service
from app.api.errors import error_response
from app.schemas import (
    APIErrorResponse,
    DetectionDTO,
    IssueCreate,
    IssueDTO,
    IssueFeatureCollection,
    IssueListQuery,
    IssueMapQuery,
    IssueUpdate,
    Page,
)
from app.services.contracts import InfraScanService, IssueNotFoundError

router = APIRouter(prefix="/issues", tags=["issues"])

_ISSUE_NOT_FOUND_RESPONSES = {
    404: {"model": APIErrorResponse, "description": "Unknown Issue"},
}


def _issue_not_found() -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        "ISSUE_NOT_FOUND",
        "The issue was not found.",
        "issue_id",
    )


@router.get(
    "/map",
    response_model=IssueFeatureCollection,
    summary="Located Issues in a viewport as a GeoJSON FeatureCollection",
)
async def list_issue_map(
    query: Annotated[IssueMapQuery, Query()],
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueFeatureCollection:
    """One Point feature per Issue, with coordinates as [longitude, latitude].

    Issues without coordinates are excluded (they remain in the review queue).
    At most `limit` features are returned; `meta.truncated` reports whether
    more Issues matched the viewport and filters.
    """
    return await service.list_issue_map(
        query.bounding_box(), query.filters(), query.limit
    )


@router.get("/review-queue", response_model=Page[IssueDTO])
async def list_review_queue(
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[IssueDTO]:
    return await service.list_review_queue(page, page_size)


@router.get("", response_model=Page[IssueDTO])
async def list_issues(
    query: Annotated[IssueListQuery, Query()],
    service: Annotated[InfraScanService, Depends(get_service)],
) -> Page[IssueDTO]:
    """Issues matching the filters, including unlocated ones unless a bounding box is given."""
    return await service.list_issues(
        query.filters(), query.bounding_box(), query.page, query.page_size
    )


@router.post("", response_model=IssueDTO, status_code=status.HTTP_201_CREATED)
async def create_issue(
    issue: IssueCreate,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO:
    return await service.create_issue(issue)


@router.get(
    "/{issue_id}", response_model=IssueDTO, responses=_ISSUE_NOT_FOUND_RESPONSES
)
async def get_issue(
    issue_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO | JSONResponse:
    try:
        return await service.get_issue(issue_id)
    except IssueNotFoundError:
        return _issue_not_found()


@router.get(
    "/{issue_id}/detections",
    response_model=Page[DetectionDTO],
    responses=_ISSUE_NOT_FOUND_RESPONSES,
)
async def list_issue_detections(
    issue_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[DetectionDTO] | JSONResponse:
    """Supporting detections, each with its raw predicted class preserved."""
    try:
        return await service.list_issue_detections(issue_id, page, page_size)
    except IssueNotFoundError:
        return _issue_not_found()


@router.patch("/{issue_id}", response_model=IssueDTO)
async def update_issue(
    issue_id: UUID,
    update: IssueUpdate,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO:
    return await service.update_issue(issue_id, update)
