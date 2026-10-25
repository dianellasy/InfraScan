from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_service
from app.schemas import (
    AnalyticsOverviewDTO,
    DamageDistributionDTO,
    GeographicAggregateDTO,
    RoadConditionDTO,
)
from app.services.contracts import InfraScanService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=AnalyticsOverviewDTO)
async def get_analytics_overview(
    service: Annotated[InfraScanService, Depends(get_service)],
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> AnalyticsOverviewDTO:
    return await service.get_analytics_overview(from_date, to_date)


@router.get("/road-conditions", response_model=list[RoadConditionDTO])
async def get_road_conditions(
    service: Annotated[InfraScanService, Depends(get_service)],
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    severity: str | None = None,
) -> list[RoadConditionDTO]:
    return await service.get_road_conditions(from_date, to_date, severity)


@router.get("/geographic", response_model=list[GeographicAggregateDTO])
async def get_geographic_analytics(
    service: Annotated[InfraScanService, Depends(get_service)],
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    classification: str | None = None,
    severity: str | None = None,
    min_latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    min_longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    max_latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    max_longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> list[GeographicAggregateDTO]:
    return await service.get_geographic_analytics(
        from_date,
        to_date,
        classification,
        severity,
        min_latitude,
        min_longitude,
        max_latitude,
        max_longitude,
    )


@router.get("/damage-distribution", response_model=list[DamageDistributionDTO])
async def get_damage_distribution(
    service: Annotated[InfraScanService, Depends(get_service)],
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> list[DamageDistributionDTO]:
    return await service.get_damage_distribution(from_date, to_date)
