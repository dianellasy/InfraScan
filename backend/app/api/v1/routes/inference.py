import base64
import json
import logging
import os
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.auth import CurrentUser, PrivilegedUser
from app.api.dependencies import get_service
from app.api.errors import error_response as _error_response
from app.api.video_validation import (
    EmptyVideo,
    MissingGps,
    VideoTooLarge,
    looks_like_iso_bmff,
    validate_video_upload,
)
from app.schemas import (
    APIErrorResponse,
    DirectJobSubmission,
    DirectUploadRequest,
    DirectUploadTicket,
    InferenceJobAccepted,
    InferenceSubmissionMetadata,
    ResolvedVideoSubmission,
    VideoSubmissionMetadata,
)
from app.services.contracts import (
    InfraScanService,
    SubmissionDependencyUnavailableError,
    UploadAlreadySubmittedError,
    UploadNotFoundError,
    UploadTooLargeError,
)
from inference.celery.celery_app import celery_app
from inference.video import MAX_VIDEO_BYTES, VideoRejected

router = APIRouter(prefix="/inference", tags=["inference"])
logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGE_LONGEST_SIDE = 4032
MAX_IMAGE_SHORTEST_SIDE = 3024
_READ_CHUNK_SIZE = 1024 * 1024
_HTTP_CONTENT_TOO_LARGE = 413
_HTTP_UNPROCESSABLE_CONTENT = 422
VIDEO_OFFSET_FIELD = "camera_utc_offset_minutes"
IMAGE_CONTENT_TYPE = "image/jpeg"
VIDEO_CONTENT_TYPE = "video/mp4"
# Direct uploads are validated after a download to the function's temp
# directory, so a deployment can cap videos below the 1 GB contract limit.
DIRECT_UPLOAD_MAX_VIDEO_BYTES = min(
    int(os.environ.get("INFRASCAN_DIRECT_UPLOAD_MAX_VIDEO_BYTES", MAX_VIDEO_BYTES)),
    MAX_VIDEO_BYTES,
)
_IMAGE_ONLY_FIELDS = frozenset({"captured_at", "latitude", "longitude"})

DEFAULT_WEIGHT_PATH = os.environ.get(
    "INFRASCAN_WEIGHT_PATH",
    str(Path(__file__).resolve().parents[4] / "inference" / "weights" / "best.onnx"),
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


def _decode_metadata(raw_metadata: str) -> dict[str, object]:
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
    return decoded


def _parse_video_metadata(decoded: dict[str, object]) -> VideoSubmissionMetadata:
    for name in decoded:
        if name in _IMAGE_ONLY_FIELDS:
            raise _ContractError(
                _HTTP_UNPROCESSABLE_CONTENT,
                "INVALID_METADATA",
                "MP4 time and location come from the embedded GPS track; "
                "send only camera_utc_offset_minutes.",
                f"metadata.{name}",
            )
    try:
        return VideoSubmissionMetadata.model_validate(decoded)
    except ValidationError as exc:
        first_error = exc.errors(include_url=False)[0]
        location = first_error.get("loc", ())
        name = str(location[0]) if location else VIDEO_OFFSET_FIELD
        if first_error.get("type") == "extra_forbidden":
            message = "MP4 metadata may contain only camera_utc_offset_minutes."
        else:
            message = (
                "camera_utc_offset_minutes must be an integer multiple of 15 "
                "between -720 and 840."
            )
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT, "INVALID_METADATA", message, f"metadata.{name}"
        ) from exc


def _parse_metadata(decoded: dict[str, object]) -> InferenceSubmissionMetadata:
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
) -> tuple[StarletteUploadFile, dict[str, object]]:
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
        if request.scope.get("state", {}).get("upload_too_large"):
            raise _ContractError(
                _HTTP_CONTENT_TOO_LARGE,
                "FILE_TOO_LARGE",
                "The upload exceeds the 1,000,000,000-byte request limit.",
                "file",
            ) from exc
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

    return file_parts[0], _decode_metadata(metadata_parts[0])


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


async def _read_head(upload: StarletteUploadFile) -> bytes:
    await upload.seek(0)
    head = await upload.read(16)
    await upload.seek(0)
    return head


async def _validate_video(
    upload: StarletteUploadFile, metadata: VideoSubmissionMetadata
) -> ResolvedVideoSubmission:
    if upload.size is not None and upload.size > MAX_VIDEO_BYTES:
        raise _ContractError(
            _HTTP_CONTENT_TOO_LARGE,
            "FILE_TOO_LARGE",
            "The uploaded video exceeds 1,000,000,000 bytes.",
            "file",
        )
    try:
        validated = await run_in_threadpool(
            validate_video_upload, upload.file, metadata.camera_utc_offset_minutes
        )
    except EmptyVideo as exc:
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "EMPTY_FILE",
            "The uploaded file must not be empty.",
            "file",
        ) from exc
    except VideoTooLarge as exc:
        raise _ContractError(
            _HTTP_CONTENT_TOO_LARGE,
            "FILE_TOO_LARGE",
            "The uploaded video exceeds 1,000,000,000 bytes.",
            "file",
        ) from exc
    except VideoRejected as exc:
        logger.info("Rejected MP4 upload: %s", exc.code)
        raise _ContractError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
            if exc.kind == "unsupported"
            else _HTTP_UNPROCESSABLE_CONTENT,
            exc.code,
            exc.message,
            "file",
        ) from exc
    except MissingGps as exc:
        logger.info("Rejected MP4 upload without a usable A119 GPS track")
        raise _ContractError(
            _HTTP_UNPROCESSABLE_CONTENT,
            "MISSING_GPS_TRACK",
            "The video has no usable embedded A119 Mini 2 GPS fix. Turn on the "
            "camera's GPS and wait for a satellite fix before recording.",
            "file",
        ) from exc
    except OSError as exc:
        logger.exception("Video validation tools are unavailable")
        raise SubmissionDependencyUnavailableError from exc

    first = validated.track.first_fix
    return ResolvedVideoSubmission(
        captured_at=first.recorded_at.isoformat(),
        latitude=first.latitude,
        longitude=first.longitude,
        camera_utc_offset_minutes=metadata.camera_utc_offset_minutes,
    )


async def _validate_submission(
    upload: StarletteUploadFile, decoded: dict[str, object]
) -> InferenceSubmissionMetadata:
    """Choose the JPEG or MP4 contract from the file bytes, never the filename."""
    is_mp4 = looks_like_iso_bmff(await _read_head(upload))
    if not is_mp4 and VIDEO_OFFSET_FIELD not in decoded:
        metadata = _parse_metadata(decoded)
        await _validate_image(upload)
        return metadata

    video_metadata = _parse_video_metadata(decoded)
    return await _validate_video(upload, video_metadata)


_IMAGE_METADATA_SCHEMA = {
    "title": "Image metadata",
    "type": "object",
    "additionalProperties": False,
    "required": ["captured_at", "latitude", "longitude"],
    "properties": {
        "captured_at": {"type": "string", "format": "date-time"},
        "latitude": {"type": "number", "minimum": -90, "maximum": 90},
        "longitude": {"type": "number", "minimum": -180, "maximum": 180},
    },
}
_VIDEO_METADATA_SCHEMA = {
    "title": "MP4 metadata",
    "type": "object",
    "additionalProperties": False,
    "required": [VIDEO_OFFSET_FIELD],
    "properties": {
        VIDEO_OFFSET_FIELD: {
            "type": "integer",
            "minimum": -720,
            "maximum": 840,
            "multipleOf": 15,
            "description": (
                "The dashcam's time-zone setting as minutes from UTC, for "
                "example -480 for UTC-08:00. A119 Mini 2 records local wall "
                "time; this offset converts it to UTC."
            ),
        },
    },
}

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
                            "The media type is detected from the bytes, not the "
                            "filename or declared MIME type. JPEG: at most "
                            "5,000,000 bytes; longest side at most 4032 px and "
                            "shortest side at most 3024 px. MP4 (VIOFO A119 Mini 2): "
                            "ISO Base Media file of at most 1,000,000,000 bytes; "
                            "duration above 0 and at most 600 s (0.5 s container "
                            "rounding tolerance); exactly one 8-bit 4:2:0 H.264 "
                            "video stream within 2560x1440 in either orientation; "
                            "average and peak frame rate at most 30 fps; audio "
                            "optional and ignored; an embedded A119 Novatek "
                            "freeGPS track with at least one active fix."
                        ),
                    },
                    "metadata": {
                        "type": "string",
                        "description": (
                            "JSON object. For a JPEG: only captured_at (with a "
                            "timezone), latitude, and longitude. For an MP4: only "
                            "camera_utc_offset_minutes; the job's captured_at, "
                            "latitude, and longitude come from the first active "
                            "GPS fix."
                        ),
                        "contentMediaType": "application/json",
                        "contentSchema": {
                            "oneOf": [_IMAGE_METADATA_SCHEMA, _VIDEO_METADATA_SCHEMA]
                        },
                    },
                },
                "additionalProperties": False,
            }
        },
        # Second step of a direct upload (see POST /api/v1/inference/uploads).
        "application/json": {"schema": DirectJobSubmission.model_json_schema()},
    },
}

_ERROR_CODES = {
    400: "INVALID_MULTIPART, MULTIPLE_FILES, DUPLICATE_METADATA, UNEXPECTED_MULTIPART_FIELD",
    413: (
        "FILE_TOO_LARGE: JPEG over 5,000,000 bytes, MP4 over 1,000,000,000 bytes, "
        "or a request body over the upload limit (with or without Content-Length)"
    ),
    415: (
        "UNSUPPORTED_REQUEST_MEDIA_TYPE (not multipart/form-data); "
        "UNSUPPORTED_IMAGE_FORMAT (image metadata with non-JPEG bytes); "
        "UNSUPPORTED_MEDIA_FORMAT (MP4 metadata with non-MP4 bytes, QuickTime, "
        "H.265, non-H.264 codec, unsupported H.264 profile or pixel format, or "
        "more than one video stream)"
    ),
    422: (
        "MISSING_FILE, MISSING_METADATA, EMPTY_FILE, INVALID_METADATA_JSON, "
        "INVALID_METADATA (including any MP4 metadata error), "
        "UNKNOWN_METADATA_FIELD, MISSING_METADATA_FIELD, INVALID_CAPTURED_AT, "
        "INVALID_COORDINATE, INVALID_IMAGE, INVALID_IMAGE_DIMENSIONS, "
        "INVALID_VIDEO (corrupt or undecodable MP4), INVALID_VIDEO_DIMENSIONS "
        "(over 2560x1440), INVALID_VIDEO_FRAME_RATE (over 30 fps), "
        "INVALID_VIDEO_DURATION (empty or over 10 minutes), MISSING_GPS_TRACK "
        "(no usable A119 GPS fix)"
    ),
}


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=InferenceJobAccepted,
    summary="Submit one JPEG or A119 Mini 2 MP4 for asynchronous road-damage inference",
    description=(
        "Validates and accepts one JPEG image or one VIOFO A119 Mini 2 MP4 without "
        "waiting for model inference. Media is streamed to object storage; an MP4 "
        "is processed as one job that samples at most 60 frames. "
        "Requires an Authorization: Bearer <Supabase access token> header; the "
        "job is owned by the token's user ID, which is never taken from the "
        "request body. Track the returned application job ID with GET "
        "/api/v1/jobs/{job_id}."
    ),
    responses={
        400: {"model": APIErrorResponse, "description": _ERROR_CODES[400]},
        401: {
            "model": APIErrorResponse,
            "description": "AUTHENTICATION_REQUIRED or INVALID_TOKEN",
        },
        413: {"model": APIErrorResponse, "description": _ERROR_CODES[413]},
        415: {"model": APIErrorResponse, "description": _ERROR_CODES[415]},
        422: {"model": APIErrorResponse, "description": _ERROR_CODES[422]},
        500: {"model": APIErrorResponse, "description": "Unexpected internal failure"},
        503: {
            "model": APIErrorResponse,
            "description": (
                "SERVICE_UNAVAILABLE (not configured), "
                "AUTHENTICATION_UNAVAILABLE (token verification unavailable), or "
                "SUBMISSION_DEPENDENCY_UNAVAILABLE (database, object storage, "
                "queue, or video tools unavailable)"
            ),
        },
    },
    openapi_extra={"requestBody": _SUBMISSION_REQUEST_BODY},
)
async def submit_inference(
    request: Request,
    user: CurrentUser,
    service: InfraScanService | None = Depends(_get_submission_service),
) -> InferenceJobAccepted | JSONResponse:
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type == "application/json":
        return await _submit_direct_upload(request, user.id, service)
    try:
        upload, decoded = await _parse_multipart_request(request)
        metadata = await _validate_submission(upload, decoded)
    except _ContractError as exc:
        return _error_response(exc.status_code, exc.code, exc.message, exc.field)
    except SubmissionDependencyUnavailableError:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SUBMISSION_DEPENDENCY_UNAVAILABLE",
            "A required submission dependency is unavailable.",
        )

    if service is None:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "The inference submission service is not configured.",
        )

    try:
        return await service.submit_inference(upload, metadata, user.id)
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


def _direct_upload_limit(content_type: str) -> int:
    return MAX_IMAGE_BYTES if content_type == IMAGE_CONTENT_TYPE else DIRECT_UPLOAD_MAX_VIDEO_BYTES


def _too_large(content_type: str) -> JSONResponse:
    kind = "image" if content_type == IMAGE_CONTENT_TYPE else "video"
    return _error_response(
        _HTTP_CONTENT_TOO_LARGE,
        "FILE_TOO_LARGE",
        f"The {kind} exceeds {_direct_upload_limit(content_type):,} bytes.",
        "size_bytes",
    )


def _unavailable() -> JSONResponse:
    return _error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "SUBMISSION_DEPENDENCY_UNAVAILABLE",
        "A required submission dependency is unavailable.",
    )


@router.post(
    "/uploads",
    status_code=status.HTTP_201_CREATED,
    response_model=DirectUploadTicket,
    summary="Get a signed URL to upload one JPEG or MP4 straight to object storage",
    description=(
        "Step 1 of a direct upload, used by the web app so large videos never pass "
        "through the API. PUT the file to `upload_url` with exactly `headers`, then "
        "start inference with POST /api/v1/inference/jobs and a JSON body "
        "{upload_id, content_type, filename, metadata}. The same JPEG and MP4 "
        "checks as the multipart upload run on the uploaded object."
    ),
    responses={
        413: {"model": APIErrorResponse, "description": "FILE_TOO_LARGE"},
        503: {"model": APIErrorResponse, "description": "Object storage unavailable"},
    },
)
async def create_direct_upload(
    body: DirectUploadRequest,
    user: CurrentUser,
    service: InfraScanService | None = Depends(_get_submission_service),
) -> DirectUploadTicket | JSONResponse:
    if body.size_bytes > _direct_upload_limit(body.content_type):
        return _too_large(body.content_type)
    if service is None:
        return _unavailable()
    try:
        return await service.create_direct_upload(body.content_type, body.size_bytes, user.id)
    except SubmissionDependencyUnavailableError:
        return _unavailable()


async def _submit_direct_upload(
    request: Request, user_id: UUID, service: InfraScanService | None
) -> InferenceJobAccepted | JSONResponse:
    try:
        body = DirectJobSubmission.model_validate_json(await request.body())
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first.get("loc", ())) or None
        return _error_response(
            _HTTP_UNPROCESSABLE_CONTENT,
            "INVALID_REQUEST",
            "The JSON body must be {upload_id, content_type, filename?, metadata}.",
            field,
        )
    if service is None:
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "The inference submission service is not configured.",
        )

    try:
        local, size = await service.fetch_direct_upload(
            body.upload_id, body.content_type, user_id, _direct_upload_limit(body.content_type)
        )
    except UploadNotFoundError:
        return _error_response(
            _HTTP_UNPROCESSABLE_CONTENT,
            "UPLOAD_NOT_FOUND",
            "No uploaded file was found for this upload_id. Upload it first.",
            "upload_id",
        )
    except UploadAlreadySubmittedError:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "UPLOAD_ALREADY_SUBMITTED",
            "A job was already started for this upload_id.",
            "upload_id",
        )
    except UploadTooLargeError:
        return _too_large(body.content_type)
    except SubmissionDependencyUnavailableError:
        return _unavailable()

    upload = StarletteUploadFile(
        file=local,
        size=size,
        filename=body.filename,
        headers=Headers({"content-type": body.content_type}),
    )
    try:
        try:
            metadata = await _validate_submission(upload, dict(body.metadata))
            is_video = isinstance(metadata, ResolvedVideoSubmission)
            if is_video != (body.content_type == VIDEO_CONTENT_TYPE):
                raise _ContractError(
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    "CONTENT_TYPE_MISMATCH",
                    "The uploaded bytes do not match content_type.",
                    "content_type",
                )
        except _ContractError as exc:
            await service.discard_direct_upload(body.upload_id, body.content_type, user_id)
            return _error_response(exc.status_code, exc.code, exc.message, exc.field)
        except SubmissionDependencyUnavailableError:
            return _unavailable()

        try:
            return await service.submit_direct_upload(
                body.upload_id, body.content_type, body.filename, metadata, user_id
            )
        except SubmissionDependencyUnavailableError:
            return _unavailable()
        except Exception:
            logger.exception("Unexpected direct-upload submission failure")
            return _error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "INTERNAL_ERROR",
                "An unexpected internal error occurred.",
            )
    finally:
        await upload.close()


@router.post(
    "/smoke-test",
    status_code=status.HTTP_200_OK,
    summary="Smoke test the RabbitMQ + Celery + YOLO inference pipeline",
    description="Developer and municipal administrator only: runs real inference.",
)
async def smoke_test_inference(_user: PrivilegedUser) -> dict:
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
