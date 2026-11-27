import os

from celery import Celery

# Production task. Its only argument is the application job_id as a string.
PROCESS_INFERENCE_JOB_TASK = "infrascan.process_inference_job"

# On Vercel (which sets VERCEL=1) Celery runs on Vercel Queues: the build
# installs the `vercel://` broker, and each task runs in a private,
# queue-triggered function built from `worker.py` (see pyproject.toml).
# Locally and in Docker Compose it uses RabbitMQ and PostgreSQL as before.
ON_VERCEL = bool(os.environ.get("VERCEL"))

celery_app = Celery(
    "infrascan",
    broker=os.environ.get(
        "CELERY_BROKER_URL",
        "vercel://" if ON_VERCEL else "amqp://infrascan:infrascan_dev@localhost:5672//",
    ),
    backend=os.environ.get(
        "CELERY_RESULT_BACKEND",
        "vercel-runtime-cache://"
        if ON_VERCEL
        else "db+postgresql+psycopg://infrascan:infrascan_dev@localhost:5432/infrascan",
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

if ON_VERCEL:
    # A queue lease must outlive the longest job (a 60-frame video), and the
    # function's own 300 s limit on the Hobby plan.
    celery_app.conf.broker_transport_options = {"lease_duration": 300}
