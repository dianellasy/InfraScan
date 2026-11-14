from __future__ import annotations

from uuid import UUID

from celery import Celery

from inference.celery.celery_app import PROCESS_INFERENCE_JOB_TASK


class CeleryInferenceQueue:
    """`InferenceQueueAdapter` that publishes by task name.

    Publishing by name keeps the API process free of the model stack. Celery
    assigns its own task ID; it is not the application job ID.
    """

    def __init__(self, celery_app: Celery) -> None:
        self._celery_app = celery_app

    def enqueue(self, job_id: UUID) -> None:
        self._celery_app.send_task(PROCESS_INFERENCE_JOB_TASK, args=[str(job_id)])
