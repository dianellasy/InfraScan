from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import EvidenceMedia, InputMedia


class InputMediaRepository:
    """PostgreSQL metadata for validated uploads stored in object storage."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def store(
        self,
        media_id: UUID,
        object_storage_key: str,
        content_type: str,
        filename: str | None,
    ) -> InputMedia:
        media = InputMedia(
            media_id=media_id,
            data=None,
            object_storage_key=object_storage_key,
            content_type=content_type,
            filename=filename,
        )
        self._session.add(media)
        self._session.flush()
        return media


class EvidenceMediaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        job_id: UUID,
        media_id: UUID,
        object_storage_key: str,
        content_type: str,
        filename: str | None,
    ) -> EvidenceMedia:
        evidence = EvidenceMedia(
            media_id=media_id,
            job_id=job_id,
            data=None,
            object_storage_key=object_storage_key,
            content_type=content_type,
            filename=filename,
        )
        self._session.add(evidence)
        self._session.flush()
        return evidence
