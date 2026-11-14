from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.adapters.ports import StoredMedia
from app.models import InferenceJob, InferenceJobStatus
from app.schemas import InferenceSubmissionMetadata


class InferenceJobRepository:
    """Persistence for `InferenceJob` rows. Callers own the transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_pending(
        self,
        media: StoredMedia,
        metadata: InferenceSubmissionMetadata,
        geolocation: dict[str, Any] | None = None,
    ) -> InferenceJob:
        """`metadata` carries the effective source GPS to persist."""
        job = InferenceJob(
            input_media_id=media.media_id,
            status=InferenceJobStatus.PENDING.value,
            captured_at=metadata.captured_at,
            # Decimal(str(float)) keeps the shortest decimal the client sent
            # instead of the binary float expansion.
            latitude=Decimal(str(metadata.latitude)),
            longitude=Decimal(str(metadata.longitude)),
            geolocation=geolocation,
        )
        self._session.add(job)
        self._session.flush()
        return job

    def get(self, job_id: UUID) -> InferenceJob | None:
        return self._session.get(InferenceJob, job_id)

    def get_for_update(self, job_id: UUID) -> InferenceJob | None:
        """Load and row-lock a job so concurrent claims serialize."""
        return self._session.get(
            InferenceJob, job_id, with_for_update=True, populate_existing=True
        )

    def exists(self, job_id: UUID) -> bool:
        return bool(
            self._session.scalar(
                select(exists().where(InferenceJob.job_id == job_id))
            )
        )

    def mark_running(self, job: InferenceJob, started_at: datetime) -> None:
        job.status = InferenceJobStatus.RUNNING.value
        job.started_at = started_at

    def mark_completed(
        self,
        job: InferenceJob,
        model_version: str,
        completed_at: datetime,
        geolocation: dict[str, Any] | None = None,
    ) -> None:
        job.status = InferenceJobStatus.COMPLETED.value
        job.model_version = model_version
        job.completed_at = completed_at
        if geolocation is not None:
            # Assign a new dict; in-place JSON mutation is not change-tracked.
            job.geolocation = geolocation

    def mark_failed(self, job_id: UUID) -> bool:
        """Fail a job that has not finished. Returns whether a row changed."""
        result = self._session.execute(
            update(InferenceJob)
            .where(
                InferenceJob.job_id == job_id,
                InferenceJob.status.in_(
                    (InferenceJobStatus.PENDING.value, InferenceJobStatus.RUNNING.value)
                ),
            )
            .values(status=InferenceJobStatus.FAILED.value)
            .execution_options(synchronize_session=False)
        )
        return result.rowcount > 0
