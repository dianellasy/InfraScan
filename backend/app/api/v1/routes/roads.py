from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_service
from app.schemas import DetectionDTO, Page, RoadDTO
from app.services.contracts import InfraScanService

router = APIRouter(prefix="/roads", tags=["roads"])


@router.get("", response_model=Page[RoadDTO])
async def list_roads(
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    min_latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    min_longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    max_latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    max_longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> Page[RoadDTO]:
    return await service.list_roads(
        page,
        page_size,
        min_latitude,
        min_longitude,
        max_latitude,
        max_longitude,
    )


@router.get("/{road_id}", response_model=RoadDTO)
async def get_road(
    road_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> RoadDTO:
    return await service.get_road(road_id)


@router.get("/{road_id}/issues", response_model=Page[DetectionDTO])
async def list_road_issues(
    road_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[DetectionDTO]:
    return await service.list_road_issues(road_id, page, page_size)
