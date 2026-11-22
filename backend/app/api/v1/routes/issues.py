from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from app.api.auth import CurrentJobAccess, PrivilegedUser
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
    access: CurrentJobAccess,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueFeatureCollection:
    """One Point feature per Issue, with coordinates as [longitude, latitude].

    Issues without coordinates are excluded (they remain in the review queue).
    At most `limit` features are returned; `meta.truncated` reports whether
    more Issues matched the viewport and filters. Issues are shared by every
    signed-in user; `evidence_thumbnail_url` is chosen only from evidence the
    caller may open, and `job_id` matches only jobs the caller may access.
    """
    return await service.list_issue_map(
        query.bounding_box(), query.filters(), query.limit, access
    )


@router.get("/review-queue", response_model=Page[IssueDTO])
async def list_review_queue(
    access: CurrentJobAccess,
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[IssueDTO]:
    return await service.list_review_queue(page, page_size, access)


@router.get("", response_model=Page[IssueDTO])
async def list_issues(
    query: Annotated[IssueListQuery, Query()],
    access: CurrentJobAccess,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> Page[IssueDTO]:
    """Issues matching the filters, including unlocated ones unless a bounding box is given."""
    return await service.list_issues(
        query.filters(), query.bounding_box(), query.page, query.page_size, access
    )


@router.post(
    "",
    response_model=IssueDTO,
    status_code=status.HTTP_201_CREATED,
    description="Developer and municipal administrator only.",
)
async def create_issue(
    issue: IssueCreate,
    _user: PrivilegedUser,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO:
    return await service.create_issue(issue)


@router.get(
    "/{issue_id}", response_model=IssueDTO, responses=_ISSUE_NOT_FOUND_RESPONSES
)
async def get_issue(
    issue_id: UUID,
    access: CurrentJobAccess,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO | JSONResponse:
    try:
        return await service.get_issue(issue_id, access)
    except IssueNotFoundError:
        return _issue_not_found()


@router.get(
    "/{issue_id}/detections",
    response_model=Page[DetectionDTO],
    responses=_ISSUE_NOT_FOUND_RESPONSES,
)
async def list_issue_detections(
    issue_id: UUID,
    access: CurrentJobAccess,
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[DetectionDTO] | JSONResponse:
    """Supporting detections the caller may access, each with its raw predicted class.

    `total` counts only those detections; the Issue's `detection_count` counts all.
    """
    try:
        return await service.list_issue_detections(issue_id, page, page_size, access)
    except IssueNotFoundError:
        return _issue_not_found()


@router.patch(
    "/{issue_id}",
    response_model=IssueDTO,
    description="Developer and municipal administrator only.",
)
async def update_issue(
    issue_id: UUID,
    update: IssueUpdate,
    _user: PrivilegedUser,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO:
    return await service.update_issue(issue_id, update)
