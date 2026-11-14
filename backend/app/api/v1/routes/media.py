from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import get_service
from app.api.errors import error_response
from app.schemas import APIErrorResponse
from app.services.contracts import InfraScanService, MediaKind, MediaNotFoundError

router = APIRouter(prefix="/media", tags=["media"])

_EXTENSIONS = {"image/jpeg": "jpg"}
_IMAGE_RESPONSES = {
    200: {"content": {"image/jpeg": {}}, "description": "Stored image bytes"},
    404: {"model": APIErrorResponse, "description": "Unknown media"},
}


async def _media_response(
    service: InfraScanService, kind: MediaKind, media_id: UUID
) -> Response:
    try:
        media = await service.get_media(kind, media_id)
    except MediaNotFoundError:
        return error_response(
            status.HTTP_404_NOT_FOUND,
            "MEDIA_NOT_FOUND",
            "The media was not found.",
            "media_id",
        )
    extension = _EXTENSIONS.get(media.content_type, "bin")
    return Response(
        content=media.data,
        media_type=media.content_type,
        headers={
            # Stored media rows are never modified, so a media ID's bytes are stable.
            "Cache-Control": "private, max-age=86400, immutable",
            "Content-Disposition": f'inline; filename="{kind}-{media.media_id}.{extension}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/evidence/{media_id}",
    name="get_evidence_media",
    response_class=Response,
    responses=_IMAGE_RESPONSES,
    summary="Annotated evidence JPEG produced by one inference job",
)
async def get_evidence_media(
    media_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> Response:
    return await _media_response(service, "evidence", media_id)


@router.get(
    "/inputs/{media_id}",
    name="get_input_media",
    response_class=Response,
    responses=_IMAGE_RESPONSES,
    summary="Original submitted JPEG",
)
async def get_input_media(
    media_id: UUID,
    service: Annotated[InfraScanService, Depends(get_service)],
) -> Response:
    return await _media_response(service, "input", media_id)
