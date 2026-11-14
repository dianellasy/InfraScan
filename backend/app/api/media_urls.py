from uuid import UUID

from fastapi import Request

from app.services.contracts import MediaKind

MEDIA_ROUTE_NAMES: dict[MediaKind, str] = {
    "evidence": "get_evidence_media",
    "input": "get_input_media",
}


class RequestMediaUrls:
    """Absolute media URLs based on the incoming request's host and root path."""

    def __init__(self, request: Request) -> None:
        self._request = request

    def media_url(self, kind: MediaKind, media_id: UUID) -> str:
        return str(self._request.url_for(MEDIA_ROUTE_NAMES[kind], media_id=str(media_id)))
