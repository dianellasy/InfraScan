from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EvidenceMedia, InputMedia


class InputMediaRepository:
    """PostgreSQL binary storage for validated uploads (`MediaStorageAdapter`)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def store(self, data: bytes, content_type: str, filename: str | None) -> InputMedia:
        media = InputMedia(data=data, content_type=content_type, filename=filename)
        self._session.add(media)
        self._session.flush()
        return media

    def get(self, media_id: UUID) -> InputMedia | None:
        return self._session.get(InputMedia, media_id)


class EvidenceMediaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, job_id: UUID, data: bytes, content_type: str, filename: str | None
    ) -> EvidenceMedia:
        evidence = EvidenceMedia(
            job_id=job_id, data=data, content_type=content_type, filename=filename
        )
        self._session.add(evidence)
        self._session.flush()
        return evidence

    def get(self, media_id: UUID) -> EvidenceMedia | None:
        return self._session.get(EvidenceMedia, media_id)

    def id_for_job(self, job_id: UUID) -> UUID | None:
        """The job's annotated image ID, without loading the image bytes."""
        return self._session.scalar(
            select(EvidenceMedia.media_id)
            .where(EvidenceMedia.job_id == job_id)
            .order_by(EvidenceMedia.media_id)
            .limit(1)
        )
