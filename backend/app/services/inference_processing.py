from __future__ import annotations

import logging
import math
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.adapters.ports import MediaStorageAdapter
from app.models import DETECTION_SEVERITIES, InferenceJobStatus
from app.repositories import (
    DetectionRecordRepository,
    EvidenceMediaRepository,
    InferenceJobRepository,
    NewDetection,
)
from app.services.issue_linking import FrameDetections, IssueLinker, IssueMatchPolicy
from app.services.object_keys import evidence_media_key
from inference.geolocation import (
    CameraProfile,
    PROTOTYPE_HEADING_DEG,
    SAMPLE_CAMERA_PROFILE,
    a119_mini2_1080p_profile,
    geolocate_detection,
)
from inference.telemetry import GpsTrack, locate_frame, read_a119_track
from inference.video import (
    MAX_VIDEO_BYTES,
    extract_frames,
    inspect_video,
    select_sample_pts,
)

logger = logging.getLogger(__name__)

EVIDENCE_CONTENT_TYPE = "image/jpeg"
VIDEO_CONTENT_TYPE = "video/mp4"
# Job coordinates are stored as NUMERIC; allow for decimal round-tripping.
FIRST_FIX_TOLERANCE_DEG = 1e-6

# (image bytes, filename) -> (v0-mvp result JSON, annotated JPEG bytes)
InferenceRunner = Callable[[bytes, str | None], tuple[Mapping[str, Any], bytes]]


class InferenceOutputError(ValueError):
    """The pipeline returned output that does not match the v0-mvp contract."""


class VideoJobError(RuntimeError):
    """The stored video no longer satisfies the contract it was accepted under."""


@dataclass(frozen=True)
class ParsedInference:
    model_version: str
    detections: list[NewDetection]


@dataclass(frozen=True)
class ClaimedJob:
    content_type: str
    data: bytes | None
    object_storage_key: str | None
    filename: str | None
    captured_at: datetime
    latitude: float
    longitude: float


@dataclass(frozen=True)
class EvidenceResult:
    """One uploaded evidence image and the detections that cite it.

    `observed_at` is when the image or frame was captured, in UTC.
    """

    media_id: UUID
    object_storage_key: str
    filename: str
    frame_timestamp: float | None
    observed_at: datetime
    detections: list[NewDetection]


def frame_observed_at(
    track: GpsTrack, first_fix_utc: datetime, frame_seconds: float
) -> datetime:
    """UTC capture time of a frame on the clip's GPS-aligned timeline.

    `first_fix_utc` is the job's `captured_at`: the API converted the first
    fix with the submitted camera offset, which the worker does not have.
    Every kept fix satisfies `fix time - frame_offset_seconds = constant`
    (see `inference.telemetry.track`), so this also holds for frames before
    the first fix and inside GPS outages.
    """
    offset = frame_seconds - track.first_fix.frame_offset_seconds
    return first_fix_utc + timedelta(seconds=offset)


@dataclass
class _Attempt:
    """Objects uploaded by one processing attempt, for cleanup on failure."""

    uploaded_keys: list[str] = field(default_factory=list)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InferenceOutputError(f"{name} must be a number")
    if not math.isfinite(value):
        raise InferenceOutputError(f"{name} must be finite")
    return float(value)


def parse_inference_output(result: Mapping[str, Any]) -> ParsedInference:
    """Convert `inference_service` output into rows, preserving model values."""
    model_version = result.get("model_version")
    if not isinstance(model_version, str) or not model_version:
        raise InferenceOutputError("model_version must be a nonempty string")

    detections = []
    for index, item in enumerate(result.get("detections") or ()):
        # `class_ids` is a scalar class ID despite its plural name.
        class_id = item.get("class_ids")
        if isinstance(class_id, bool) or not isinstance(class_id, int):
            raise InferenceOutputError(f"detections[{index}].class_ids must be an int")

        severity = item.get("severity")
        if severity not in DETECTION_SEVERITIES:
            raise InferenceOutputError(f"detections[{index}].severity is not allowed")

        confidence = _number(item.get("confidence"), f"detections[{index}].confidence")
        if not 0 <= confidence <= 1:
            raise InferenceOutputError(f"detections[{index}].confidence is out of range")

        bbox = item.get("bbox_xyxy")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise InferenceOutputError(f"detections[{index}].bbox_xyxy must have 4 values")

        detections.append(
            NewDetection(
                issue_type=class_id,
                confidence=confidence,
                severity=severity,
                bounding_box=[
                    _number(v, f"detections[{index}].bbox_xyxy") for v in bbox
                ],
            )
        )
    return ParsedInference(model_version=model_version, detections=detections)


def _prediction_size(result: Mapping[str, Any]) -> tuple[int, int]:
    width, height = result.get("image_width"), result.get("image_height")
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise InferenceOutputError("Prediction dimensions must be positive integers")
    return width, height


class InferenceJobProcessor:
    """Runs one persisted inference job end to end.

    Short transactions only: claim the pending job (mark it running); then
    download, decode, infer, geolocate, and upload evidence with no session
    open; then save all evidence rows, detections, their Issue links (see
    `app.services.issue_linking`), and the completed status together. Only
    that last transaction takes the Issue-matching advisory lock. Ordinary
    failures mark the job failed and delete evidence objects this attempt
    uploaded. A redelivered task for a job that is no longer pending is a
    no-op. Retries and recovery of stranded jobs are deferred until after the
    MVP.

    An MP4 is one job: at most 60 real frames are sampled evenly, each with
    its own evidence image, timestamp, GPS base point, and heading.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        run_inference: InferenceRunner,
        clock: Callable[[], datetime] = _utc_now,
        *,
        media_storage: MediaStorageAdapter,
        camera_profile: CameraProfile = SAMPLE_CAMERA_PROFILE,
        heading_deg: float = PROTOTYPE_HEADING_DEG,
        video_camera_profile: CameraProfile | None = None,
        work_dir: str | Path | None = None,
        issue_policy: IssueMatchPolicy | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._run_inference = run_inference
        self._clock = clock
        self._media_storage = media_storage
        self._camera_profile = camera_profile
        if not math.isfinite(heading_deg):
            raise ValueError("Prototype heading must be finite")
        self._heading_deg = heading_deg
        self._video_profile = video_camera_profile or a119_mini2_1080p_profile()
        self._work_dir = work_dir
        self._issue_policy = issue_policy or IssueMatchPolicy.from_env()

    def process(self, job_id: UUID) -> InferenceJobStatus | None:
        """Return the job's final status, or None if this call did not run it."""
        try:
            claimed = self._claim(job_id)
        except Exception:
            logger.exception("Could not start inference job %s", job_id)
            raise
        if claimed is None:
            return None

        attempt = _Attempt()
        try:
            if claimed.content_type == VIDEO_CONTENT_TYPE:
                model_version, evidence, detected_at = self._process_video(
                    job_id, claimed, attempt
                )
            else:
                model_version, evidence, detected_at = self._process_image(
                    job_id, claimed, attempt
                )
        except Exception:
            logger.exception("Inference failed for job %s", job_id)
            self._fail(job_id, attempt)
            return InferenceJobStatus.FAILED

        try:
            self._save_results(job_id, model_version, evidence, detected_at)
        except Exception:
            logger.exception("Could not save inference results for job %s", job_id)
            self._fail(job_id, attempt)
            return InferenceJobStatus.FAILED

        logger.info(
            "Inference job %s completed with %d detection(s) across %d evidence image(s)",
            job_id,
            sum(len(item.detections) for item in evidence),
            len(evidence),
        )
        return InferenceJobStatus.COMPLETED

    # --- Image jobs ---

    def _process_image(
        self, job_id: UUID, claimed: ClaimedJob, attempt: _Attempt
    ) -> tuple[str, list[EvidenceResult], datetime]:
        image = claimed.data
        if image is None:
            image = self._media_storage.get(claimed.object_storage_key)
        result, annotated_jpeg = self._run_inference(image, claimed.filename)
        detected_at = self._clock()
        parsed = parse_inference_output(result)
        width, height = _prediction_size(result)
        detections = [
            replace(
                detection,
                location=geolocate_detection(
                    detection.bounding_box, width, height,
                    latitude=claimed.latitude, longitude=claimed.longitude,
                    heading_deg=self._heading_deg, profile=self._camera_profile,
                ),
            )
            for detection in parsed.detections
        ]
        logger.info(
            "Job %s prototype geolocation profile=%s heading=%s located=%d/%d",
            job_id, self._camera_profile.name, self._heading_deg,
            sum(d.location is not None for d in detections), len(detections),
        )
        evidence = self._upload_evidence(
            job_id,
            annotated_jpeg,
            None,
            claimed.captured_at,
            f"annotated-{job_id}.jpg",
            detections,
            attempt,
        )
        return parsed.model_version, [evidence], detected_at

    # --- Video jobs ---

    def _process_video(
        self, job_id: UUID, claimed: ClaimedJob, attempt: _Attempt
    ) -> tuple[str, list[EvidenceResult], datetime]:
        with tempfile.TemporaryDirectory(prefix="infrascan-job-", dir=self._work_dir) as tmp:
            work = Path(tmp)
            video_path = work / "input.mp4"
            with open(video_path, "wb") as target:
                if claimed.data is not None:
                    target.write(claimed.data)
                else:
                    self._media_storage.download_to_file(
                        claimed.object_storage_key, target, MAX_VIDEO_BYTES
                    )

            # Re-validate: the stored object is never trusted on API-time checks alone.
            info, timeline = inspect_video(video_path)
            track = read_a119_track(video_path, 0, info.duration_seconds)
            self._check_first_fix(track, claimed)

            frames_dir = work / "frames"
            frames_dir.mkdir()
            frames = extract_frames(
                video_path, info, timeline, select_sample_pts(timeline.pts), frames_dir
            )
            video_path.unlink()

            model_version: str | None = None
            evidence: list[EvidenceResult] = []
            unlocated_frames = 0
            stem = Path(claimed.filename or "video").stem
            for frame in frames:
                milliseconds = round(frame.timestamp_seconds * 1000)
                image = frame.path.read_bytes()
                frame.path.unlink()
                result, annotated = self._run_inference(image, f"{stem}-{milliseconds:08d}.jpg")
                parsed = parse_inference_output(result)
                if model_version is None:
                    model_version = parsed.model_version
                elif parsed.model_version != model_version:
                    raise InferenceOutputError("model_version changed within one video job")
                detections, located = self._geolocate_frame(
                    parsed, result, track, frame.timestamp_seconds
                )
                unlocated_frames += not located
                evidence.append(
                    self._upload_evidence(
                        job_id,
                        annotated,
                        frame.timestamp_seconds,
                        frame_observed_at(
                            track, claimed.captured_at, frame.timestamp_seconds
                        ),
                        f"annotated-{job_id}-{milliseconds:08d}.jpg",
                        detections,
                        attempt,
                    )
                )
        detected_at = self._clock()
        logger.info(
            "Job %s video profile=%s sampled_frames=%d frames_without_gps=%d "
            "detections=%d located_detections=%d gps_fixes=%d",
            job_id, self._video_profile.name, len(frames), unlocated_frames,
            sum(len(item.detections) for item in evidence),
            sum(d.location is not None for item in evidence for d in item.detections),
            len(track.fixes),
        )
        return model_version, evidence, detected_at

    def _check_first_fix(self, track: GpsTrack, claimed: ClaimedJob) -> None:
        first = track.first_fix
        if (
            abs(first.latitude - claimed.latitude) > FIRST_FIX_TOLERANCE_DEG
            or abs(first.longitude - claimed.longitude) > FIRST_FIX_TOLERANCE_DEG
        ):
            raise VideoJobError("The stored video's first GPS fix differs from the job")

    def _geolocate_frame(
        self,
        parsed: ParsedInference,
        result: Mapping[str, Any],
        track: GpsTrack,
        frame_seconds: float,
    ) -> tuple[list[NewDetection], bool]:
        width, height = _prediction_size(result)
        base = locate_frame(track.fixes, frame_seconds)
        profile = self._video_profile
        same_orientation = (width >= height) == (profile.image_width >= profile.image_height)
        if base is None or base.heading_deg is None or not same_orientation:
            return [replace(d, location=None) for d in parsed.detections], False
        return [
            replace(
                detection,
                location=geolocate_detection(
                    detection.bounding_box, width, height,
                    latitude=base.latitude, longitude=base.longitude,
                    heading_deg=base.heading_deg, profile=profile,
                ),
            )
            for detection in parsed.detections
        ], True

    # --- Shared persistence ---

    def _upload_evidence(
        self,
        job_id: UUID,
        annotated_jpeg: bytes,
        frame_timestamp: float | None,
        observed_at: datetime,
        filename: str,
        detections: list[NewDetection],
        attempt: _Attempt,
    ) -> EvidenceResult:
        media_id = uuid4()
        key = evidence_media_key(job_id, media_id, frame_timestamp)
        # Recorded first: a failed upload may still leave a partial object.
        attempt.uploaded_keys.append(key)
        self._media_storage.put(annotated_jpeg, key, EVIDENCE_CONTENT_TYPE)
        return EvidenceResult(
            media_id, key, filename, frame_timestamp, observed_at, detections
        )

    def _claim(self, job_id: UUID) -> ClaimedJob | None:
        with self._session_factory() as session, session.begin():
            jobs = InferenceJobRepository(session)
            job = jobs.get_for_update(job_id)
            if job is None:
                logger.error("Inference job %s does not exist; skipping", job_id)
                return None
            if job.status != InferenceJobStatus.PENDING:
                logger.warning(
                    "Inference job %s is %s, not pending; skipping", job_id, job.status
                )
                return None
            jobs.mark_running(job, started_at=self._clock())
            media = job.input_media
            return ClaimedJob(
                content_type=media.content_type,
                data=media.data,
                object_storage_key=media.object_storage_key,
                filename=media.filename,
                captured_at=job.captured_at,
                latitude=float(job.latitude),
                longitude=float(job.longitude),
            )

    def _save_results(
        self,
        job_id: UUID,
        model_version: str,
        evidence: list[EvidenceResult],
        detected_at: datetime,
    ) -> None:
        with self._session_factory() as session, session.begin():
            jobs = InferenceJobRepository(session)
            job = jobs.get_for_update(job_id)
            if job is None or job.status != InferenceJobStatus.RUNNING:
                raise RuntimeError("job is no longer running")
            media = EvidenceMediaRepository(session)
            records = DetectionRecordRepository(session)
            frames = []
            for item in evidence:
                row = media.add(
                    job_id,
                    data=None,
                    content_type=EVIDENCE_CONTENT_TYPE,
                    filename=item.filename,
                    media_id=item.media_id,
                    object_storage_key=item.object_storage_key,
                    frame_timestamp=item.frame_timestamp,
                )
                saved = records.add_many(
                    job_id, row, item.detections, detected_at, item.observed_at
                )
                frames.append(FrameDetections(job_id, row.media_id, item.observed_at, saved))
            if any(frame.records for frame in frames):
                linker = IssueLinker(session, self._issue_policy)
                linker.lock()
                summary = linker.link(frames)
                logger.info(
                    "Job %s linked %d detection(s): %d new Issue(s), %d existing Issue(s) "
                    "updated, %d unmapped detection(s) left without an Issue",
                    job_id, summary.linked, len(summary.created_issue_ids),
                    len(summary.updated_issue_ids), summary.unmapped,
                )
            jobs.mark_completed(job, model_version, completed_at=self._clock())

    def _fail(self, job_id: UUID, attempt: _Attempt) -> None:
        for key in attempt.uploaded_keys:
            try:
                self._media_storage.delete(key)
            except Exception:
                logger.exception(
                    "Could not delete evidence object %s for failed job %s", key, job_id
                )
        try:
            with self._session_factory() as session, session.begin():
                InferenceJobRepository(session).mark_failed(job_id)
        except Exception:
            logger.exception("Could not mark inference job %s failed", job_id)
