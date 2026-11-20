# Agent prompt: implement A119 Mini 2 MP4 ingestion end to end

You are working in `/home/cornelius/Desktop/InfraScan` on the `feat/geo_deduplication` branch. Implement production-quality MP4 ingestion for InfraScan, including VIOFO A119 Mini 2 GPS metadata, R2 object storage, sampled frame inference, persistence, API/UI behavior, deterministic synthetic seed data, and documentation.

Do not stop at a design or partial scaffold. Complete the feature, integrate it into the current application, and verify it. Do not commit or push unless the user asks. Preserve all existing user changes: the worktree is dirty and contains unrelated issue-map work. Never reset, revert, clean, or overwrite unrelated changes.

## Outcome

A user can select an A119 Mini 2 `.mp4` in the frontend, submit it, receive a normal asynchronous job ID, poll the existing job endpoint, and retrieve detections through the existing detections endpoint just as with JPEG jobs. Each video detection must include the sampled frame's relative timestamp, an evidence JPEG stored in R2, and a location computed from that frame's embedded GPS fix and heading.

JPEG behavior must remain backward compatible.

## Inspect the repository before editing

Start by reading the current versions and diffs of these files. Do not assume committed `HEAD` represents all intended work:

- `backend/app/api/v1/routes/inference.py`
- `backend/app/api/upload_limit.py`
- `backend/app/services/inference_jobs.py`
- `backend/app/services/inference_processing.py`
- `backend/app/services/issues.py`
- `backend/app/adapters/ports.py`
- `backend/app/adapters/r2_storage.py`
- `backend/app/repositories/media.py`
- `backend/app/repositories/detections.py`
- `backend/app/models/media.py`
- `backend/app/models/inference_job.py`
- `backend/app/models/detection_record.py`
- `backend/inference/celery/tasks.py`
- `backend/inference/service.py`
- `backend/inference/geolocation/`
- `backend/main.py`
- `compose.yaml`
- `frontend/InfraScan frontend/src/App.tsx`
- all migrations and relevant tests

There is important repository archaeology:

- Commit `5ea6954` contains an earlier video implementation that was later removed. Inspect it with `git show`, especially `backend/app/services/video.py`, the upload route, processing service, and `backend/tests/test_video_pipeline.py`. Reuse sound ideas only; do not restore the commit wholesale.
- Earlier R2 commits include `811e9f7`, `8bc0a8a`, and `6337cb2`.
- Current dirty tests already describe part of the intended R2 model (`object_storage_key`, nullable legacy `data`, R2 cleanup, injected storage), while current models/services/migrations do not fully match them. Reconcile this deliberately rather than weakening the tests.
- The removed video attempt trusted the declared MIME type in some paths, had a stub `_validate_video`, did not parse A119 GPS, and restricted actual A119 files without explaining the required camera settings. Do not repeat those errors.

Before changing code, record the baseline failures and distinguish existing failures from regressions. Do not modify tests merely to make them pass unless the product contract truly changed.

## Device facts and evidence

Use these facts as the supported-device contract:

1. VIOFO documents the A119 Mini 2 as a single front camera with MP4 output, a built-in GPS logger, a 140-degree field of view, and GPS location, speed, and time available during playback. It supports multiple resolutions and frame rates, including `1920x1080` at `30 fps`, and loop lengths through 10 minutes.
2. The official manual says GPS data is included in recorded videos when a GPS signal is available. It also exposes a camera time-zone setting that must be adjusted manually for daylight saving time.
3. Independent open-source implementations tested against real A119 Mini 2 recordings identify the telemetry as Novatek `freeGPS` Type-3 records embedded roughly once per second. This is proprietary binary metadata, not a standard MP4 GPS track, so ordinary `ffprobe` output is insufficient.
4. The useful record fields are fix status, timestamp, latitude, longitude, speed, and course/heading. Type-3 coordinates are stored as NMEA-style `DDMM.MMMM` / `DDDMM.MMMM` floating-point values with hemisphere flags. Speed is stored in knots and must be converted to km/h. Course is degrees clockwise from true north.
5. Only active fixes (`A`) are usable. Void/pre-lock fixes (`V`) must not be emitted as real locations. Some A119 Mini 2 cold starts contain pre-lock records with a different clock basis followed by a multi-hour jump when the first valid fix arrives. Do not anchor the clip to those pre-lock timestamps.
6. Newer A119 Mini 2 recordings have been observed with a 14-digit local timestamp plus a 6-digit counter, for example `20240619153957_000049.mp4`. Treat filenames as hints only, never as proof of media type or authoritative telemetry.

Primary and implementation references:

- Official product page: <https://www.viofo.com/products/viofo-a119-mini-2-voice-control-2k-60fps-5ghz-wifi-dash-camera-with-sony-starvis-2-image-sensor-hdr-super-night-sensibility>
- Official manual: <https://cdn.shopify.com/s/files/1/0729/7486/0324/files/A119MINI2Englishmanual_compressed.pdf?v=1768874048>
- Real-device format coverage: <https://github.com/everydashcam/everydashcam/blob/main/docs/gps-format-coverage.md>
- A119 Mini 2-tested extractor: <https://github.com/ibragari/route_overlay>
- Independent Novatek parser: <https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/QuickTimeStream.pl>

Pin the exact upstream commit used to confirm binary offsets and parsing rules in project documentation. Respect licenses. Do not copy code blindly; implement a small, isolated parser with tests and document every field/offset. If upstream sources disagree, fail closed and document the uncertainty rather than guessing.

## Supported upload contract

Keep the current JPEG contract unchanged:

- actual JPEG bytes, not filename or declared MIME
- nonempty and at most 5,000,000 bytes
- longest side at most 4032 pixels and shortest side at most 3024 pixels
- valid full decode with EXIF orientation accounted for
- required image metadata remains `captured_at`, `latitude`, and `longitude`

Add this MP4 contract, matching the limits already presented by the frontend and the earlier design:

- actual ISO Base Media MP4 bytes; do not trust extension or multipart MIME
- nonempty and at most 1,000,000,000 bytes, inclusive
- duration greater than zero and at most 600 seconds
- exactly one H.264 video stream
- dimensions must fit within 1920 by 1080 in either orientation
- average and peak frame rates must be positive and no greater than 30 fps
- audio is optional and must not affect inference
- a valid embedded A119/Novatek Type-3 GPS track with at least one active fix is required
- at most 60 frames are sampled for inference

The A119 Mini 2 supports higher resolutions, 60 fps, and possibly other encoding choices, but they are outside this first contract. Update the frontend copy to tell the user to configure the camera to `1920x1080`, `30 fps`, H.264, and a loop duration no longer than 10 minutes. Reject 2K, 60 fps, and H.265 cleanly instead of accepting uploads the worker cannot process.

For MP4 submissions, replace manual capture coordinates with embedded telemetry. The video metadata form should carry an editable camera UTC offset, because the A119 Mini 2 camera time-zone setting is manual and observed records may contain local wall time stored as if it were UTC. Use an unambiguous signed field such as `camera_utc_offset_minutes` (`-480` for UTC-08:00). Default it in the frontend from the browser, label it clearly as the dashcam setting, and let the user change it.

Derive the job's `captured_at`, `latitude`, and `longitude` from the first valid GPS fix, applying the submitted camera offset to the raw local timestamp. Keep image submission metadata unchanged. Do not silently use `(0, 0)`, file modification time, filename time, or a hardcoded location as a production fallback.

Use stable API errors. At minimum distinguish:

- request too large: `413 FILE_TOO_LARGE`
- unsupported container/codec/profile: `415 UNSUPPORTED_MEDIA_FORMAT`
- corrupt or undecodable MP4: `422 INVALID_VIDEO`
- no usable A119 GPS fixes: `422 MISSING_GPS_TRACK`
- invalid video metadata/time-zone offset: `422 INVALID_METADATA`

The exact messages should be useful to the frontend and documented in OpenAPI.

## Safe, bounded media handling

Never read a 1 GB upload into a Python `bytes` value. Never store MP4 payloads in PostgreSQL. Never send media through Celery or base64.

Use the seekable spooled upload file for validation and stream it to R2 with a file-object/multipart API. Extend `MediaStorageAdapter` and `R2MediaStorageAdapter` with stream-oriented upload and bounded download methods. Use boto3's managed file-object transfer or an equivalent bounded implementation. Keep small JPEG helpers if useful, but route video through streaming APIs.

The API process may inspect the local spooled file before uploading. Run blocking file, R2, database, `ffprobe`, and FFmpeg work off the event loop. The worker should download the R2 object to a temporary file with a hard byte cap and clean the file in all outcomes.

Invoke `ffprobe`/FFmpeg only on a local path. Use argument arrays, no shell, `-nostdin`, a restricted protocol whitelist, bounded timeouts, bounded captured output, and deterministic stream selection. Prevent URL/protocol access through crafted metadata. Validate frame timestamps and decoded frame counts. Keep temporary decoded output bounded and delete it after processing.

Keep `UploadLimitMiddleware` as an early request-body guard for both requests with and without `Content-Length`, including multipart overhead. Convert its streaming overflow into the same 413 JSON envelope instead of leaking parser exceptions.

## R2 and database model

Finish the intended R2 migration for both JPEG and MP4 paths:

- New input media and evidence media go to R2.
- PostgreSQL stores the media ID, canonical content type, original filename, object key, and media-specific metadata. It does not store new payload bytes.
- Preserve read compatibility with legacy rows that still contain `BYTEA` data.
- Add `object_storage_key` to input and evidence media with an additive Alembic migration. Make legacy `data` nullable and add a database check requiring at least one of `data` or `object_storage_key`.
- Add nullable, nonnegative `frame_timestamp` to evidence media. It is null for image evidence and the source-frame PTS in seconds for video evidence.
- `DetectionRecord.frame_timestamp` must match its evidence frame timestamp for video and stay null for images.
- Use deterministic, collision-safe object keys without a leading slash, for example `input/{media_id}.mp4` and `evidence/{job_id}/{timestamp_ms}-{media_id}.jpg`.
- Wire one storage abstraction into API submission, worker input loading, result persistence, and evidence retrieval. The current evidence endpoint must fetch R2-backed evidence rather than assuming `media.data` is present.
- API and worker containers must both receive the R2 configuration and instantiate the adapter. Unit tests must inject an in-memory fake and never contact real R2.
- Do not log credentials, signed URLs, raw GPS tracks, or full object contents.

Handle cross-system failures explicitly:

- If R2 input upload succeeds but the database insert fails, delete the uploaded object.
- If the database commit succeeds but queue publication fails, mark the job failed and retain the input for diagnosis/retry according to the current policy.
- If any frame inference, evidence upload, or result transaction fails, mark the job failed, roll back detection/evidence rows, and delete evidence objects uploaded by that attempt.
- A cleanup failure must be logged with object key and job ID without hiding the primary failure.

Do not hold a database transaction or row lock while downloading, decoding, running inference, or uploading evidence.

## A119 Mini 2 GPS parser

Create an isolated parser module under `backend/inference/telemetry/` (or another clear inference-owned location) with typed values such as:

```python
@dataclass(frozen=True)
class GpsFix:
    frame_offset_seconds: float
    recorded_at: datetime
    latitude: float
    longitude: float
    speed_kmh: float | None
    course_deg: float | None
```

Requirements:

- Parse the MP4/Novatek `gps ` lookup/index when it is present and structurally valid.
- Support a bounded, chunked `freeGPS ` marker scan fallback for files whose index is missing or unusable. Handle markers crossing chunk boundaries and never load the full file.
- Validate every referenced offset and block size against file bounds before reading.
- Recognize only the confirmed Type-3 layout for this feature. Do not misparse unrelated Novatek variants as A119 data.
- Decode little/big endian fields exactly as confirmed by the pinned independent implementation.
- Convert NMEA degree-minute floats to decimal degrees and apply `N/S/E/W` signs.
- Convert knots to km/h with factor `1.852`.
- Normalize course to `[0, 360)` and reject nonfinite values.
- Reject impossible dates, coordinates, offsets, block sizes, and truncated fields.
- Exclude void/pre-lock fixes. Drop exact repeated records caused by stale GPS updates, but preserve legitimate stationary fixes with advancing timestamps.
- Detect the documented cold-start clock discontinuity. Do not use pre-lock timestamps to determine clip start. Use valid-fix order and video-relative timing as the primary alignment; use absolute time only after applying the camera UTC offset.
- Preserve roughly one-second telemetry cadence without assuming it is perfectly uniform.

Map sampled frame PTS values to telemetry with a deterministic policy:

- interpolate latitude and longitude only between bracketing valid fixes when the gap is small (choose and document a conservative maximum, such as 5 seconds)
- use the closest fix only within a small documented tolerance
- do not bridge long GPS outages or tunnels with an invented straight line
- interpolate circular headings correctly across 359/0 degrees, or derive heading from adjacent coordinates when course is absent and movement is sufficient
- a sampled frame without a trustworthy fix may still be inferred, but its detections must have `location = null`; the job must report/log how many sampled frames lacked GPS

For located frames, call the existing detection projection with that frame's GPS base point and heading. Add a clearly named provisional A119 Mini 2 camera profile for the supported 1920x1080 mode. The official 140-degree value does not by itself prove calibrated horizontal/vertical intrinsics or mounting pitch/height, so document the approximation and keep extrinsics configurable. Do not present projected pothole coordinates as hardware-calibrated until actual footage is measured.

## Video processing

Use one asynchronous application job for the entire clip. Preserve the existing `pending -> running -> completed|failed` lifecycle and existing polling/detection endpoints.

The worker flow should be:

1. Claim and mark the job running in a short transaction.
2. Download the input from R2 to a bounded temporary file (or use legacy bytes for an old row).
3. Re-inspect the video and parse the full GPS track; never trust only the API-time inspection.
4. Select at most 60 real source frames evenly across valid presentation timestamps, including the beginning and end when possible. Store the actual normalized PTS, not a requested theoretical timestamp.
5. Decode selected frames in a single bounded FFmpeg pass.
6. Run the existing image model on each frame without changing its image result contract.
7. Annotate each selected frame using the existing evidence path.
8. Attach the frame timestamp and frame-specific geolocation/heading to detections.
9. Upload evidence JPEGs to R2 and persist evidence/detections atomically with job completion. Clean uploaded evidence if persistence fails.
10. Remove all temporary files.

Do not write to the shared `OUTPUT_PATH`; concurrent jobs must not overwrite one another. Keep model loading cached per worker process as it is now.

It is acceptable to persist one evidence JPEG per sampled frame for traceability. If you instead persist only frames with detections, document that choice and ensure zero-detection jobs still complete correctly. Whichever policy you choose must be consistent across code, tests, API responses, and cleanup.

Order job detections deterministically by `frame_timestamp`, then confidence descending, then ID. Existing image ordering must remain stable.

## Synthetic A119 Mini 2 seed generator

There is no real camera sample yet. Create a deterministic generator, not a hand-authored opaque binary. Suggested location: `backend/scripts/generate_a119_mini2_seed.py`.

The generator must produce:

- a playable MP4 named like a newer A119 Mini 2 clip, e.g. `20261120123000_000001.MP4`
- `1920x1080`, 30 fps, H.264, `yuv420p`, and a short duration suitable for tests (10-15 seconds is enough)
- deterministic visual frames; use an existing road fixture if legally available, otherwise use a generated moving road-like/test pattern and do not claim visual fidelity to the Sony sensor
- one Type-3 `freeGPS` record per second following the independently verified binary layout
- a structurally valid `gps ` lookup/index when the real format uses one, with offsets updated after MP4 atom layout is finalized
- a plausible deterministic route near the existing Stanton/Anaheim seed area, realistic city speed, course, active fix state, timestamps, and western-hemisphere signs
- a JSON manifest containing the exact expected video properties and decoded GPS fixes

Also generate focused fixtures for:

- initial `V` no-fix records followed by the first `A` fix and a clock jump
- an exact duplicate/stale record
- a short GPS gap
- a corrupt/truncated block
- MP4 with no GPS
- southern/eastern and northern/western sign handling
- heading interpolation across north

Keep generated test artifacts small. Do not commit a large 1080p MP4 if a reproducible generator can create it during tests. A tiny committed binary GPS fixture is acceptable when it gives independent golden bytes. Do not make parser and generator share the same packing/unpacking constants without an independent golden assertion; otherwise the same wrong offset could make both pass.

Validate synthetic compatibility with an independent implementation when available, such as ExifTool `-ee` or a pinned A119 Mini 2-tested parser. Make this an optional developer verification if the tool is not part of runtime/CI. Clearly label the generated media as `synthetic-a119-mini2-like`, not a real camera export.

## Frontend behavior

- Continue to accept `.jpg`, `.jpeg`, and `.mp4` in the chooser.
- Keep the immediate 5 MB JPEG and 1 GB MP4 size checks, while treating backend validation as authoritative.
- For JPEG, keep capture time, latitude, and longitude inputs required.
- For MP4, hide/disable manual latitude and longitude and show that location comes from embedded A119 GPS.
- For MP4, collect/edit the camera UTC offset described above.
- Tell users the required A119 settings: MP4, H.264, 1920x1080, 30 fps, GPS enabled, and at most a 10-minute clip.
- Display backend validation errors as it does today.
- Keep job polling and detection rendering compatible. Show frame timestamp for video detections and keep it empty for images.

Do not infer video type solely from the filename for the server. Filename checks in the browser are only convenience checks.

## Tests and verification

Add or repair focused tests covering at least:

### Validation

- MP4 accepted when multipart MIME is wrong but bytes are valid
- renamed JPEG/non-MP4 rejected as video
- corrupt MP4, H.265, multiple video streams, 60 fps, oversized dimensions, over-600-second duration, empty upload, and over-1-GB request
- request limit with and without `Content-Length`
- no GPS and all-void GPS return `MISSING_GPS_TRACK`
- JPEG validation remains unchanged

### Telemetry

- independent golden Type-3 bytes decode to exact timestamp, decimal coordinates, km/h, and course
- hemisphere signs, degree-minute conversion, invalid values, truncation, bogus offsets, duplicates, cold-start jump, gaps, and north-crossing headings
- marker scan handles chunk boundaries and enforces bounded reads
- frame-to-GPS matching follows the documented gap/tolerance policy

### R2/database

- new input/evidence rows store object keys and no new payload bytes
- legacy BYTEA rows still process and serve evidence
- API and worker use injected storage off the event loop where appropriate
- upload/DB/queue/inference/evidence/commit failures produce the intended status and orphan cleanup
- migrations upgrade and downgrade cleanly from the current Alembic head (`4b7d2e9c5a18`) without destroying existing media or issue data

### Processing/API

- deterministic sampling returns no more than 60 unique ordered actual PTS values
- every video detection and evidence row has the correct frame timestamp
- detections use per-frame GPS/course and no longer use one fixed job coordinate/heading for the entire video
- frames inside long GPS gaps produce null locations
- zero-detection videos complete successfully
- one failure cannot leave a completed job with partial rows
- image job behavior and existing issue/evidence endpoints still pass
- an end-to-end test covers HTTP -> R2 fake -> Celery worker -> database -> job/detections/evidence response

Run the relevant backend and frontend test/build/lint commands that already exist in the repository. Also run migration SQL/render checks and a local generated-seed smoke test through the real FFmpeg/frame pipeline. Do not require real R2 credentials or a real camera file for automated tests.

## Documentation and hardware handoff

Update README/OpenAPI with:

- JPEG and MP4 contracts
- required A119 Mini 2 camera settings
- R2 object layout and required environment variables, using placeholders only
- GPS metadata fields and time-zone behavior
- frame sampling policy and its consequence that unsampled damage may be missed
- synthetic fixture limitations
- instructions for generating and inspecting the seed clip

Add a short real-hardware validation checklist for when an actual A119 Mini 2 clip is available:

1. Copy the original file directly from the SD card. Do not re-export it through an editor or app that may strip proprietary GPS atoms.
2. Record firmware version, camera resolution/fps/codec, loop setting, GPS setting, time zone, HDR state, and whether a satellite lock was visible.
3. Compare parser output to the VIOFO player for start time, several coordinates, speed, and route direction.
4. Compare `ffprobe` properties to the accepted contract.
5. Run a dry-run inspection that does not upload or persist the file.
6. Anonymize or exclude private route data before committing any fixture.
7. Add a real-sample-derived regression fixture only after confirming license/privacy and stripping location identity.

## Definition of done

The feature is complete only when:

- a generated A119-like MP4 passes backend validation and upload from the frontend
- the original MP4 is streamed into R2, not PostgreSQL or Celery
- the worker downloads it, extracts verified GPS fixes, samples and infers frames, and stores evidence in R2
- the job reaches `completed` and the existing job/detection/evidence APIs return frame-aware located results
- expected failures reach `failed` or return a precise upload error without orphaned R2 objects or partial database results
- JPEG submission still works
- migrations, backend tests, frontend tests/build/lint, and the generated-seed smoke path pass
- documentation accurately separates manufacturer facts, independently observed binary behavior, design choices, and assumptions awaiting real hardware

At the end, report: files changed, schema/API changes, validation limits, GPS decoding decisions, R2/cleanup behavior, tests run with exact results, remaining risks, and the exact steps for validating the first real A119 Mini 2 clip.
