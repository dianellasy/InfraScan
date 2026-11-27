from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.auth import CurrentJobAccess
from app.api.dependencies import get_service
from app.api.errors import error_response
from app.schemas import APIErrorResponse
from app.services.contracts import (
    EvidenceNotFoundError,
    EvidenceUnavailableError,
    InfraScanService,
)

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get(
    "/{media_id}",
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "Evidence image bytes"},
        401: {"model": APIErrorResponse, "description": "Missing, invalid, or expired token"},
        404: {
            "model": APIErrorResponse,
            "description": "Unknown evidence image, or one from a job the caller may not access",
        },
        503: {
            "model": APIErrorResponse,
            "description": "EVIDENCE_UNAVAILABLE: the stored image cannot be read from object storage",
        },
    },
)
async def get_evidence_image(
    media_id: UUID,
    access: CurrentJobAccess,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> Response:
    """Requires a bearer token, so browsers must fetch it and display a blob URL."""
    try:
        image = await service.get_evidence_image(media_id, access)
    except EvidenceNotFoundError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "EVIDENCE_NOT_FOUND",
            "The evidence image was not found.",
            "media_id",
        )
    except EvidenceUnavailableError:
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "EVIDENCE_UNAVAILABLE",
            "The evidence image is temporarily unavailable.",
            "media_id",
        )
    # Evidence rows are immutable once written. `private` keeps shared caches
    # from storing per-user images; `Vary` stops reuse across signed-in users.
    return Response(
        content=image.data,
        media_type=image.content_type,
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "Vary": "Authorization",
        },
    )
