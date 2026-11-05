import base64
import os
import time
from pathlib import Path

from celery.signals import worker_process_init

from inference.celery.celery_app import celery_app
from inference.service import DEFAULT_WEIGHT_PATH as _DEFAULT_WEIGHT_PATH
from inference.service import get_model, inference_service

DEFAULT_WEIGHT_PATH = os.environ.get("INFRASCAN_WEIGHT_PATH", _DEFAULT_WEIGHT_PATH)


@worker_process_init.connect
def preload_model(**kwargs):
    """Warm YOLO weights in each prefork child so the first task is not a cold load."""
    if Path(DEFAULT_WEIGHT_PATH).exists():
        get_model(DEFAULT_WEIGHT_PATH)


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
