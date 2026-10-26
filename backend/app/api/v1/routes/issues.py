from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_service
from app.schemas import IssueCreate, IssueDTO, IssueUpdate, Page
from app.services.contracts import InfraScanService

router = APIRouter(prefix="/issues", tags=["issues"])


@router.get("/review-queue", response_model=Page[IssueDTO])
async def list_review_queue(
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[IssueDTO]:
    return await service.list_review_queue(page, page_size)


@router.get("", response_model=Page[IssueDTO])
async def list_issues(
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    status_filter: Annotated[str, Query(alias="status")] = "accepted",
) -> Page[IssueDTO]:
    return await service.list_issues(page, page_size, status_filter)


@router.post("", response_model=IssueDTO, status_code=status.HTTP_201_CREATED)
async def create_issue(
    issue: IssueCreate,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO:
    return await service.create_issue(issue)


@router.get("/{issue_id}", response_model=IssueDTO)
async def get_issue(
    issue_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO:
    return await service.get_issue(issue_id)


@router.patch("/{issue_id}", response_model=IssueDTO)
async def update_issue(
    issue_id: UUID,
    update: IssueUpdate,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> IssueDTO:
    return await service.update_issue(issue_id, update)
