from __future__ import annotations

from uuid import UUID

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
