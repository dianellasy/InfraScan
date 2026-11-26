from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import EvidenceMedia, InputMedia


class InputMediaRepository:
    """Metadata rows for validated uploads; payloads live in object storage."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def store(
        self,
        data: bytes | None,
        content_type: str,
        filename: str | None,
        *,
        media_id: UUID | None = None,
        object_storage_key: str | None = None,
    ) -> InputMedia:
        media = InputMedia(
            data=data,
            content_type=content_type,
            filename=filename,
            object_storage_key=object_storage_key,
        )
        if media_id is not None:
            media.media_id = media_id
        self._session.add(media)
        self._session.flush()
        return media

    def exists(self, media_id: UUID) -> bool:
        return self._session.get(InputMedia, media_id) is not None


class EvidenceMediaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        job_id: UUID,
        data: bytes | None,
        content_type: str,
        filename: str | None,
        *,
        media_id: UUID | None = None,
        object_storage_key: str | None = None,
        frame_timestamp: float | None = None,
    ) -> EvidenceMedia:
        evidence = EvidenceMedia(
            job_id=job_id,
            data=data,
            content_type=content_type,
            filename=filename,
            object_storage_key=object_storage_key,
            frame_timestamp=frame_timestamp,
        )
        if media_id is not None:
            evidence.media_id = media_id
        self._session.add(evidence)
        self._session.flush()
        return evidence
