import os

from celery import Celery

# Production task. Its only argument is the application job_id as a string.
PROCESS_INFERENCE_JOB_TASK = "infrascan.process_inference_job"

celery_app = Celery(
    "infrascan",
    broker=os.environ.get(
        "CELERY_BROKER_URL",
        "amqp://infrascan:infrascan_dev@localhost:5672//",
    ),
    backend=os.environ.get(
        "CELERY_RESULT_BACKEND",
        "db+postgresql+psycopg://infrascan:infrascan_dev@localhost:5432/infrascan",
    ),
    include=["inference.celery.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
