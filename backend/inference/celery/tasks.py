import base64
import logging
import os
import time
from pathlib import Path
from uuid import UUID

from celery.signals import worker_process_init, worker_process_shutdown

from app.services.inference_processing import InferenceJobProcessor
from inference.celery import database
from inference.celery.celery_app import PROCESS_INFERENCE_JOB_TASK, celery_app
from inference.service import DEFAULT_WEIGHT_PATH as _DEFAULT_WEIGHT_PATH
from inference.service import get_model, inference_service, inference_with_annotation

DEFAULT_WEIGHT_PATH = os.environ.get("INFRASCAN_WEIGHT_PATH", _DEFAULT_WEIGHT_PATH)

logger = logging.getLogger(__name__)


@worker_process_init.connect
def preload_model(**kwargs):
    """Warm YOLO weights in each prefork child so the first task is not a cold load."""
    if Path(DEFAULT_WEIGHT_PATH).exists():
        get_model(DEFAULT_WEIGHT_PATH)


@worker_process_init.connect
def reset_database_after_fork(**kwargs):
    database.dispose_engine(close=False)


@worker_process_shutdown.connect
def close_database(**kwargs):
    database.dispose_engine()


@celery_app.task(name="lab.add")
def add(x, y):
    return x + y


@celery_app.task(name="infrascan.inference")
def inference_worker(weight_path, image_b64, filename=None):
    resolved_weight = weight_path or DEFAULT_WEIGHT_PATH
    image_bytes = base64.b64decode(image_b64)
    time.sleep(4)
    result = inference_service(resolved_weight, image_bytes, filename=filename)
    return {"status": "completed", "result": result}


def run_v0_mvp_inference(image_bytes, filename):
    return inference_with_annotation(DEFAULT_WEIGHT_PATH, image_bytes, filename=filename)


# Results live in the application tables; the Celery result backend is unused.
@celery_app.task(name=PROCESS_INFERENCE_JOB_TASK, ignore_result=True)
def process_inference_job(job_id: str) -> None:
    try:
        parsed_job_id = UUID(job_id)
    except (TypeError, ValueError, AttributeError):
        logger.error("Ignoring inference task with invalid job_id %r", job_id)
        return
    processor = InferenceJobProcessor(
        database.get_session_factory(), run_inference=run_v0_mvp_inference
    )
    processor.process(parsed_job_id)
