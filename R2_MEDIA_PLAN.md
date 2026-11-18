# R2 media inference plan

**Target:** Wednesday, November 18, 2026, 11:59 p.m. Pacific.

## Goal and proof

The API accepts JPEG images and MP4/H.264 videos, stores each original in private R2, and queues a job ID. The Celery worker reads that object from R2, runs inference, stores annotated JPEG evidence in R2, and records the job, detections, timestamps, metadata, and object keys in PostgreSQL. A successful JPEG and video request must each reach `completed` and expose their persisted detections. The database must not store bytes for new media.

Use a 1 GB video upload limit, at most 1920 × 1080 display dimensions, 30 fps, and 10 minutes as the initial contract. Sample at most 60 frames distributed over the video and retain their real timestamps. These limits bound processing work as well as upload size. For moving video, leave detection location null until a time-aligned GPS track is available; applying the submission's single coordinate to every frame would be wrong.

## Implementation order

### 1. Schema and data transition

- Check `alembic heads` on the branch that will receive the change. The current `backend-dev` head is `9e2b6d4f1a37`; a merged branch may have a newer head.
- Add nullable `object_key` columns to `input_media` and `evidence_media`. Add matching model fields. Keep `media_id`, `content_type`, and `filename`.
- Make both `data` columns nullable before inserting R2-only rows. Keep the old data for fallback or backfill. Add a constraint requiring `object_key` or `data` on each media row so an empty reference cannot be committed.
- Give new rows an explicit `media_id` before uploading, then derive the R2 key from it. The current repository only assigns the UUID when the row is flushed.
- Backfill old input and evidence bytes to deterministic keys, verify the objects, and populate keys. Drop `data` only in a later migration after old rows and application reads are verified.
- Treat downgrade of the nullable `data` change carefully: after R2-only rows exist, restoring `NOT NULL` requires copying their bytes back from R2 first. A downgrade must guard against losing or invalidating those rows.

**Review gate:** migration upgrades and downgrades cleanly on a development database with existing media rows; models and schema agree. Share the migration and model diff before changing service code.

### 2. JPEG end to end

- Keep the existing JPEG validation and 5 MB limit. Generate the media UUID before upload and derive a key such as `images/input/<media_id>.jpg`.
- In the API service, upload bytes to R2, commit the media row and pending job, then enqueue only the job ID. If the commit fails, attempt to delete the uploaded object. Handle upload failure without creating a pending job.
- In the worker, claim the job in a short transaction, then fetch the input object after closing that transaction. Run the current image model, upload annotated evidence to `images/evidence/<media_id>.jpg`, and commit evidence, detections, and completed status together. A failed fetch or upload must mark the job failed; a failed commit should attempt evidence cleanup.
- Pass the same storage adapter to API and worker composition. Remove the adapter's production `print()` and hard-coded test path.

**Review gate:** one JPEG completes through API → R2 → Celery → R2 → PostgreSQL; input and evidence bytes can be fetched by their saved keys. Check one missing-object failure.

### 3. Video ingress and storage

- Branch validation by actual media type in the inference route. Probe video content and enforce format, dimensions, frame rate, duration, and size. Enforce the upload body limit before multipart parsing.
- Install the video probe/decoder tools in the API and worker images. Neither current Dockerfile installs `ffprobe`/`ffmpeg` explicitly.
- Add file/stream transfer methods to the storage adapter. The current `put(bytes)` and `get() -> bytes` interface must not load a 1 GB video into memory. Use a temporary file or file-like stream and remove it after use.
- Save the video under a key such as `videos/input/<media_id>.mp4`, with content type and filename in `input_media`. The job queue still carries only the job ID.

**Review gate:** a valid MP4 creates a pending job and an R2 object; invalid, corrupt, and oversized uploads are rejected before a job is queued.

### 4. Video inference and evidence

- The worker downloads the video to temporary storage, decodes frames incrementally, and samples at most 60 frames across its duration. Convert each selected frame into the image runner's expected input and record its timestamp in seconds.
- Update submission metadata and the processor's geolocation branch for video. The current schema requires one latitude/longitude and the processor geolocates every detection from that one point; video results need null locations until per-frame coordinates are available.
- Persist detections with `frame_timestamp`. Store annotated JPEG evidence for sampled frames in R2 and associate each detection with its frame's evidence row. Consider adding `frame_timestamp` to `evidence_media` so even frames with no detections retain an identity.
- Commit the video job as completed only after all selected frames and results are persisted. Clean temporary files and handle partial R2 evidence uploads on failure.

**Review gate:** one MP4 reaches `completed`; detections refer to the right frame timestamps and R2 evidence. A missing input object or failed frame processing ends as `failed`, not stranded `running`.

### 5. Deadline verification

- Run one JPEG and one MP4 through the actual API and Celery worker with PostgreSQL and R2 configured.
- Inspect the job status, media object keys, R2 objects, evidence rows, detection rows, and video frame timestamps.
- Check upload failure, missing input object, and failed evidence upload paths.
- Update the README with the accepted formats, limits, required environment variables, and migration command.

## Current repository starting points

- Migration/model: `backend/alembic/versions/`, `backend/app/models/media.py`
- API validation and submission: `backend/app/api/v1/routes/inference.py`, `backend/app/services/inference_jobs.py`
- Worker and persistence: `backend/app/services/inference_processing.py`, `backend/app/repositories/media.py`, `backend/app/repositories/detections.py`, `backend/inference/celery/tasks.py`
- R2 adapter and process configuration: `backend/app/adapters/r2_storage.py`, `backend/main.py`, `compose.yaml`
