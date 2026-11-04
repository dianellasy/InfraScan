import base64
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from PIL import Image
from pydantic import ValidationError

from app.schemas import InferenceMetadata
from inference.celery.celery_app import celery_app

router = APIRouter(prefix="/inference", tags=["inference"])

DEFAULT_WEIGHT_PATH = os.environ.get(
    "INFRASCAN_WEIGHT_PATH",
    str(Path(__file__).resolve().parents[4] / "inference" / "weights" / "best.pt"),
)
SMOKE_IMAGE_PATH = (
    Path(__file__).resolve().parents[4] / "inference" / "data" / "uploads" / "multi.png"
)


def _dispatch_and_await(image_bytes: bytes, filename: str | None) -> dict:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    async_result = celery_app.send_task(
        "infrascan.inference",
        args=[DEFAULT_WEIGHT_PATH, image_b64, filename],
    )
    task_output = async_result.get(timeout=120)
    print(json.dumps(task_output, indent=2), flush=True)
    return {
        "task_id": async_result.id,
        **task_output,
    }


@router.post(
    "/jobs",
    status_code=status.HTTP_200_OK,
    summary="Submit media from frontend for road-damage inference",
)
async def submit_inference(
    files: Annotated[list[UploadFile], File(description="Images or videos to analyze")],
    metadata: Annotated[
        str | None, Form(description="Optional JSON-encoded inference metadata")
    ] = None,
) -> dict:
    if metadata:
        try:
            parsed_metadata = InferenceMetadata.model_validate_json(metadata)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.errors(),
            ) from exc
    else:
        parsed_metadata = None

    results = []
    for upload in files:
        image_bytes = await upload.read()
        output = _dispatch_and_await(image_bytes, upload.filename)
        results.append({"filename": upload.filename, **output})

    response = {
        "metadata": parsed_metadata.model_dump(mode="json") if parsed_metadata else None,
        "results": results,
    }
    print("Prod inference endpoint results:", json.dumps(response, indent=2), flush=True)
    return response


@router.post(
    "/smoke-test",
    status_code=status.HTTP_200_OK,
    summary="Smoke test the RabbitMQ + Celery + YOLO inference pipeline",
)
async def smoke_test_inference() -> dict:
    if SMOKE_IMAGE_PATH.exists():
        image_bytes = SMOKE_IMAGE_PATH.read_bytes()
        filename = SMOKE_IMAGE_PATH.name
    else:
        buf = BytesIO()
        Image.new("RGB", (640, 640), color=(128, 128, 128)).save(buf, format="PNG")
        image_bytes = buf.getvalue()
        filename = "smoke_test.png"

    output = _dispatch_and_await(image_bytes, filename)
    print("Smoke-test inference result:", json.dumps(output, indent=2), flush=True)
    return output
