from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_service
from app.api.errors import error_response
from app.schemas import APIErrorResponse
from app.services.contracts import EvidenceNotFoundError, InfraScanService

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get(
    "/{media_id}",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "Evidence image bytes"},
        404: {"model": APIErrorResponse, "description": "Unknown evidence image"},
    },
)
async def get_evidence_image(
    media_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> Response:
    try:
        image = await service.get_evidence_image(media_id)
    except EvidenceNotFoundError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "EVIDENCE_NOT_FOUND",
            "The evidence image was not found.",
            "media_id",
        )
    # Evidence rows are immutable once written.
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )
