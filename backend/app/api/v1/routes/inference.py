import base64
import json
import logging
import os
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.dependencies import get_service
from app.schemas import (
    APIError,
    APIErrorResponse,
    InferenceJobAccepted,
    InferenceSubmissionMetadata,
)
from app.services.contracts import (
    InfraScanService,
    SubmissionDependencyUnavailableError,
)
from inference.celery.celery_app import celery_app

router = APIRouter(prefix="/inference", tags=["inference"])
logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGE_LONGEST_SIDE = 4032
MAX_IMAGE_SHORTEST_SIDE = 3024
_READ_CHUNK_SIZE = 1024 * 1024
_HTTP_CONTENT_TOO_LARGE = 413
_HTTP_UNPROCESSABLE_CONTENT = 422

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


@dataclass
class _ContractError(Exception):
    status_code: int
    code: str
    message: str
    field: str | None = None


def _error_response(
    status_code: int, code: str, message: str, field: str | None = None
) -> JSONResponse:
    payload = APIErrorResponse(
        error=APIError(code=code, message=message, field=field)
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
    )


def _get_submission_service(request: Request) -> InfraScanService | None:
    """Use the shared resolver while retaining this endpoint's error envelope."""
    try:
        return get_service(request)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            return None
        raise


def _reject_nonfinite_json_number(value: str) -> None:
    raise ValueError(f"{value} is not a valid JSON number")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _parse_metadata(raw_metadata: str) -> InferenceSubmissionMetadata:
    try:
        decoded = json.loads(
            raw_metadata,
            parse_constant=_reject_nonfinite_json_number,
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "INVALID_METADATA_JSON",
            "Metadata must be a valid JSON object.",
            "metadata",
        ) from exc

    if not isinstance(decoded, dict):
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "INVALID_METADATA",
            "Metadata must be a JSON object.",
            "metadata",
        )

    try:
        return InferenceSubmissionMetadata.model_validate(decoded)
    except ValidationError as exc:
        first_error = exc.errors(include_url=False)[0]
        location = first_error.get("loc", ())
        metadata_field = str(location[0]) if location else None
        field_path = (
            f"metadata.{metadata_field}" if metadata_field else "metadata"
        )
        error_type = first_error.get("type")

        if error_type == "extra_forbidden":
            raise _ContractError(
                _HTTP_UNPROCESSABLE_CONTENT,
                "UNKNOWN_METADATA_FIELD",
                "Metadata contains an unknown field.",
                field_path,
            ) from exc
        if error_type == "missing":
            raise _ContractError(
                _HTTP_UNPROCESSABLE_CONTENT,
                "MISSING_METADATA_FIELD",
                "A required metadata field is missing.",
                field_path,
            ) from exc
        if metadata_field == "captured_at":
            raise _ContractError(
                _HTTP_UNPROCESSABLE_CONTENT,
                "INVALID_CAPTURED_AT",
                "captured_at must be a timezone-aware ISO 8601 datetime string.",
                field_path,
            ) from exc
        if metadata_field in {"latitude", "longitude"}:
            raise _ContractError(
                _HTTP_UNPROCESSABLE_CONTENT,
                "INVALID_COORDINATE",
                f"{metadata_field} must be a finite JSON number in range.",
                field_path,
            ) from exc
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "INVALID_METADATA",
            "Metadata is invalid.",
            field_path,
        ) from exc


async def _parse_multipart_request(
    request: Request,
) -> tuple[StarletteUploadFile, InferenceSubmissionMetadata]:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type != "multipart/form-data":
        raise _ContractError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "UNSUPPORTED_REQUEST_MEDIA_TYPE",
            "Content-Type must be multipart/form-data.",
        )

    try:
        form = await request.form()
    except Exception as exc:
        raise _ContractError(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_MULTIPART",
            "The multipart request could not be parsed.",
        ) from exc

    file_parts: list[StarletteUploadFile] = []
    metadata_parts: list[str] = []
    for name, value in form.multi_items():
        if name not in {"file", "metadata"}:
            raise _ContractError(
                status.HTTP_400_BAD_REQUEST,
                "UNEXPECTED_MULTIPART_FIELD",
                "The multipart request contains an unexpected field.",
                name,
            )
        if name == "file":
            if not isinstance(value, StarletteUploadFile):
                raise _ContractError(
                    status.HTTP_400_BAD_REQUEST,
                    "INVALID_MULTIPART",
                    "The file part must be an uploaded file.",
                    "file",
                )
            file_parts.append(value)
        else:
            if isinstance(value, StarletteUploadFile):
                raise _ContractError(
                    status.HTTP_400_BAD_REQUEST,
                    "INVALID_MULTIPART",
                    "The metadata part must be a JSON-encoded form field.",
                    "metadata",
                )
            metadata_parts.append(value)

    if len(file_parts) > 1:
        raise _ContractError(
            status.HTTP_400_BAD_REQUEST,
            "MULTIPLE_FILES",
            "Exactly one uploaded file is allowed.",
            "file",
        )
    if len(metadata_parts) > 1:
        raise _ContractError(
            status.HTTP_400_BAD_REQUEST,
            "DUPLICATE_METADATA",
            "Exactly one metadata part is allowed.",
            "metadata",
        )
    if not file_parts:
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "MISSING_FILE",
            "The file part is required.",
            "file",
        )
    if not metadata_parts:
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "MISSING_METADATA",
            "The metadata part is required.",
            "metadata",
        )

    return file_parts[0], _parse_metadata(metadata_parts[0])


def _oriented_dimensions(image: Image.Image) -> tuple[int, int]:
    width, height = image.size
    orientation = image.getexif().get(274, 1)
    if orientation in {5, 6, 7, 8}:
        return height, width
    return width, height


def _inspect_jpeg(stream: BinaryIO) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            stream.seek(0)
            with Image.open(stream) as image:
                if image.format != "JPEG":
                    raise _ContractError(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        "UNSUPPORTED_IMAGE_FORMAT",
                        "Uploaded media must contain a JPEG image.",
                        "file",
                    )

                width, height = _oriented_dimensions(image)
                if width <= 0 or height <= 0:
                    raise _ContractError(
                        _HTTP_UNPROCESSABLE_CONTENT,
                        "INVALID_IMAGE",
                        "The uploaded JPEG is invalid or corrupt.",
                        "file",
                    )

                longest_side = max(width, height)
                shortest_side = min(width, height)
                if (
                    longest_side > MAX_IMAGE_LONGEST_SIDE
                    or shortest_side > MAX_IMAGE_SHORTEST_SIDE
                ):
                    raise _ContractError(
                        _HTTP_UNPROCESSABLE_CONTENT,
                        "INVALID_IMAGE_DIMENSIONS",
                        "Image dimensions exceed the allowed maximum.",
                        "file",
                    )
                image.verify()

            # Pillow's verify() checks structure without decoding pixels. Reopen
            # and load only after dimensions are known to be within the limit.
            stream.seek(0)
            with Image.open(stream) as image:
                image.load()
    except _ContractError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "INVALID_IMAGE",
            "The uploaded JPEG is invalid or corrupt.",
            "file",
        ) from exc


async def _validate_image(upload: StarletteUploadFile) -> None:
    try:
        if upload.size is not None and upload.size > MAX_IMAGE_BYTES:
            raise _ContractError(
                _HTTP_CONTENT_TOO_LARGE,
                "FILE_TOO_LARGE",
                "The uploaded file exceeds 5,000,000 bytes.",
                "file",
            )

        total_bytes = 0
        while total_bytes <= MAX_IMAGE_BYTES:
            remaining_with_sentinel = MAX_IMAGE_BYTES - total_bytes + 1
            chunk = await upload.read(min(_READ_CHUNK_SIZE, remaining_with_sentinel))
            if not chunk:
                break
            total_bytes += len(chunk)

        if total_bytes == 0:
            raise _ContractError(
                _HTTP_UNPROCESSABLE_CONTENT,
                "EMPTY_FILE",
                "The uploaded file must not be empty.",
                "file",
            )
        if total_bytes > MAX_IMAGE_BYTES:
            raise _ContractError(
                _HTTP_CONTENT_TOO_LARGE,
                "FILE_TOO_LARGE",
                "The uploaded file exceeds 5,000,000 bytes.",
                "file",
            )

        await upload.seek(0)
        await run_in_threadpool(_inspect_jpeg, upload.file)
    finally:
        await upload.seek(0)


_SUBMISSION_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file", "metadata"],
                "properties": {
                    "file": {
                        "type": "string",
                        "format": "binary",
                        "description": (
                            "One JPEG image, at most 5,000,000 bytes; longest "
                            "side at most 4032 px and shortest side at most 3024 px."
                        ),
                    },
                    "metadata": {
                        "type": "string",
                        "description": (
                            "JSON object containing only captured_at, latitude, "
                            "and longitude. captured_at must include a timezone."
                        ),
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["captured_at", "latitude", "longitude"],
                            "properties": {
                                "captured_at": {
                                    "type": "string",
                                    "format": "date-time",
                                },
                                "latitude": {
                                    "type": "number",
                                    "minimum": -90,
                                    "maximum": 90,
                                },
                                "longitude": {
                                    "type": "number",
                                    "minimum": -180,
                                    "maximum": 180,
                                },
                            },
                        },
                    },
                },
                "additionalProperties": False,
            }
        }
    },
}


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=InferenceJobAccepted,
    summary="Submit one JPEG for asynchronous road-damage inference",
    description=(
        "Validates and accepts one image without waiting for model inference. "
        "Authentication is deferred; the future production contract will use "
        "an Authorization: Bearer <token> header and derive the user ID on the "
        "backend. Track the returned application job ID with GET "
        "/api/v1/jobs/{job_id}."
    ),
    responses={
        400: {"model": APIErrorResponse, "description": "Invalid multipart structure"},
        413: {"model": APIErrorResponse, "description": "File exceeds 5,000,000 bytes"},
        415: {"model": APIErrorResponse, "description": "Unsupported media type"},
        422: {"model": APIErrorResponse, "description": "Invalid file or metadata"},
        500: {"model": APIErrorResponse, "description": "Unexpected internal failure"},
        503: {
            "model": APIErrorResponse,
            "description": "Submission service unavailable",
        },
    },
    openapi_extra={"requestBody": _SUBMISSION_REQUEST_BODY},
)
async def submit_inference(
    request: Request,
    service: InfraScanService | None = Depends(_get_submission_service),
) -> InferenceJobAccepted | JSONResponse:
    try:
        upload, metadata = await _parse_multipart_request(request)
        await _validate_image(upload)
    except _ContractError as exc:
        return _error_response(exc.status_code, exc.code, exc.message, exc.field)

    if service is None:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "The inference submission service is not configured.",
        )

    try:
        return await service.submit_inference(upload, metadata)
    except SubmissionDependencyUnavailableError:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SUBMISSION_DEPENDENCY_UNAVAILABLE",
            "A required submission dependency is unavailable.",
        )
    except Exception:
        logger.exception("Unexpected inference submission failure")
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "An unexpected internal error occurred.",
        )


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
