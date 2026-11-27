from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, exists, select, true, update
from sqlalchemy.orm import Session

from app.adapters.ports import StoredMedia
from app.authorization import JobAccess
from app.models import InferenceJob, InferenceJobStatus
from app.schemas import InferenceSubmissionMetadata


def job_access_clause(access: JobAccess) -> ColumnElement[bool]:
    """SQL form of `JobAccess`: owner-scoped access never matches ownerless jobs."""
    if access.unrestricted:
        return true()
    return InferenceJob.user_id == access.owner_id


class InferenceJobRepository:
    """Persistence for `InferenceJob` rows. Callers own the transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_pending(
        self, media: StoredMedia, metadata: InferenceSubmissionMetadata, user_id: UUID
    ) -> InferenceJob:
        job = InferenceJob(
            user_id=user_id,
            input_media_id=media.media_id,
            status=InferenceJobStatus.PENDING.value,
            captured_at=metadata.captured_at,
            # Decimal(str(float)) keeps the shortest decimal the client sent
            # instead of the binary float expansion.
            latitude=Decimal(str(metadata.latitude)),
            longitude=Decimal(str(metadata.longitude)),
        )
        self._session.add(job)
        self._session.flush()
        return job

    def get(self, job_id: UUID) -> InferenceJob | None:
        return self._session.get(InferenceJob, job_id)

    def get_accessible(self, job_id: UUID, access: JobAccess) -> InferenceJob | None:
        """The job, or None when it does not exist or `access` does not cover it."""
        return self._session.scalar(
            select(InferenceJob).where(
                InferenceJob.job_id == job_id, job_access_clause(access)
            )
        )

    def get_for_update(self, job_id: UUID) -> InferenceJob | None:
        """Load and row-lock a job so concurrent claims serialize."""
        return self._session.get(
            InferenceJob, job_id, with_for_update=True, populate_existing=True
        )

    def exists(self, job_id: UUID, access: JobAccess | None = None) -> bool:
        clauses = [InferenceJob.job_id == job_id]
        if access is not None:
            clauses.append(job_access_clause(access))
        return bool(self._session.scalar(select(exists().where(*clauses))))

    def mark_running(self, job: InferenceJob, started_at: datetime) -> None:
        job.status = InferenceJobStatus.RUNNING.value
        job.started_at = started_at

    def mark_completed(
        self, job: InferenceJob, model_version: str, completed_at: datetime
    ) -> None:
        job.status = InferenceJobStatus.COMPLETED.value
        job.model_version = model_version
        job.completed_at = completed_at

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
