# InfraScan

Road Infrastructure Issue Detection System

Current Phase:
	- Architectural Design and Planning

## Branching

`main` is the source of truth. `frontend-dev` and `backend-dev` are the long-lived development branches.

To start a change, check out the branch for your area and create your working branch from it:

```bash
git checkout frontend-dev   # or backend-dev
git checkout -b <your-branch-name>
```

## Architecture Checklist

- [-] System Context Diagram (C4 Level 1) (DONE - 10/13)
- [-] Container Diagram (C4 Level 2) (DONE - 10/13)
- [-] Component Diagrams (Backend, Inference) (DONE - 10/15)
- [-] API Design and Interface Definitions (DONE - 10/12)
- [-] Technology Stack and Justifications (DONE - 10/12)
- [-] Database Schema / ERD (DONE - 12/14)
- [ ] Deployment Architecture (proposed edge, cloud, or hybrid) (TBD)

Still need to consider
- main statistics to display
- UI flow for frontend

## Backend API

The FastAPI contract is versioned under `/api/v1` and declares the API routes
and Pydantic DTOs described in the architecture design:

| Method | Endpoint |
| --- | --- |
| `POST` | `/api/v1/inference/smoke_test` |
| `POST` | `/api/v1/inference/jobs` |
| `GET`, `POST` | `/api/v1/issues` |
| `GET` | `/api/v1/issues/review-queue` |
| `GET`, `PATCH` | `/api/v1/issues/{issue_id}` |
| `GET` | `/api/v1/jobs` |
| `GET` | `/api/v1/jobs/{job_id}` |
| `GET` | `/api/v1/jobs/{job_id}/detections` |
| `GET` | `/api/v1/roads` |
| `GET` | `/api/v1/roads/{road_id}` |
| `GET` | `/api/v1/roads/{road_id}/issues` |
| `GET` | `/api/v1/detections` |
| `GET` | `/api/v1/detections/{detection_id}` |
| `GET` | `/api/v1/detections/{detection_id}/evidence` |
| `GET` | `/api/v1/analytics/overview` |
| `GET` | `/api/v1/analytics/road-conditions` |
| `GET` | `/api/v1/analytics/geographic` |
| `GET` | `/api/v1/analytics/damage-distribution` |

The API handlers depend on the `InfraScanService` contract in
`backend/app/services/contracts.py`. Infrastructure boundaries for media
storage, inference-job persistence, task queuing, and domain repositories are
declared in `backend/app/adapters/ports.py`. When `DATABASE_URL` is set, the
app's startup lifespan (`backend/main.py`) attaches the PostgreSQL-backed
`DatabaseInfraScanService` (`backend/app/services/inference_jobs.py`). It
implements inference submission, `GET /api/v1/jobs/{job_id}`,
`GET /api/v1/jobs/{job_id}/detections`, and the Issue reads
(`GET /api/v1/issues`, `/issues/map`, `/issues/review-queue`,
`/issues/{issue_id}`, `/issues/{issue_id}/detections`, and
`GET /api/v1/evidence/{media_id}`); the other endpoints, including Issue
create/update, return `501 NOT_IMPLEMENTED`. Without `DATABASE_URL`, no service
is attached and API calls return `503 Service Unavailable` rather than
placeholder data.

Install backend dependencies with `python -m pip install -r backend/requirements.txt`.
Run the API from the backend directory with `uvicorn main:app --reload`; the
interactive API schema is available at `/docs`.

### Image inference submission contract

`POST /api/v1/inference/jobs` accepts `multipart/form-data` and returns as soon
as the application service has accepted the job; it does not wait for model
inference. The request must contain exactly two parts:

- `file`: exactly one nonempty JPEG. The controller validates the actual image,
  not its extension or declared MIME type. The maximum size is 5,000,000 bytes
  (inclusive), the longest side is at most 4032 pixels, and the shortest side is
  at most 3024 pixels. EXIF orientation is accounted for.
- `metadata`: one JSON-encoded object containing exactly `captured_at`,
  `latitude`, and `longitude`. The timestamp must be an ISO 8601 string with a
  timezone and is normalized to UTC. Latitude is from -90 through 90 and
  longitude is from -180 through 180, in decimal degrees.

For example:

```bash
curl --request POST http://localhost:8000/api/v1/inference/jobs \
  --form 'file=@road.jpg;type=image/jpeg' \
  --form 'metadata={"captured_at":"2026-11-10T14:30:00-08:00","latitude":33.8823,"longitude":-117.8851}'
```

A successful submission returns `202 Accepted`:

```json
{
  "job_id": "7b101a70-ec2b-4c8a-a728-272fff4fd7b8",
  "status": "pending",
  "submitted_at": "2026-11-10T22:30:01Z"
}
```

The frontend can track that application job with
`GET /api/v1/jobs/{job_id}`. A Celery task ID is not a job ID.

Errors use `{"error":{"code":"...","message":"...","field":"..."}}`. Stable
codes include `INVALID_MULTIPART`, `UNEXPECTED_MULTIPART_FIELD`,
`MULTIPLE_FILES`, `DUPLICATE_METADATA`, `MISSING_FILE`, `EMPTY_FILE`,
`FILE_TOO_LARGE`, `MISSING_METADATA`, `INVALID_METADATA_JSON`,
`INVALID_METADATA`, `MISSING_METADATA_FIELD`, `UNKNOWN_METADATA_FIELD`,
`INVALID_CAPTURED_AT`, `INVALID_COORDINATE`, `UNSUPPORTED_REQUEST_MEDIA_TYPE`,
`UNSUPPORTED_IMAGE_FORMAT`, `INVALID_IMAGE_DIMENSIONS`, `INVALID_IMAGE`,
`SERVICE_UNAVAILABLE`, `SUBMISSION_DEPENDENCY_UNAVAILABLE`, and
`INTERNAL_ERROR`. MP4 submissions add the codes listed in the next section.

Authentication is intentionally not enforced in this milestone. The future
production request will carry `Authorization: Bearer <token>` and the backend
will derive the authenticated user ID; client-supplied user IDs are rejected as
unknown metadata.

### VIOFO A119 Mini 2 MP4 submission contract

The same endpoint accepts one dashcam clip. The media type is decided from the
file bytes (an ISO Base Media `ftyp` box), never from the filename or the
declared MIME type; browser-side extension checks are only a convenience.

**Required camera settings:** MP4 container, H.264 codec, `1920x1080`
(1080P) or `2560x1440` (1440P), `30 fps`, GPS on (wait for a satellite fix),
loop recording of 10 minutes or less. The camera's other modes (60 fps, H.265,
other resolutions above 1080P) are rejected cleanly rather than accepted and
failed later. 1440P geolocation is provisional until that mode is calibrated
(see [Provisional A119 Mini 2 camera profile](#provisional-a119-mini-2-camera-profile)).

- `file`: nonempty ISO Base Media MP4 of at most 1,000,000,000 bytes
  (inclusive); duration greater than 0 and at most 600 s (0.5 s container
  rounding tolerance); exactly one H.264 video stream (Constrained Baseline,
  Baseline, Main, or High; 8-bit 4:2:0 `yuv420p`/`yuvj420p`) within 1920x1080,
  or exactly 2560x1440, in either orientation (1920x1200, for example, is
  rejected); average frame rate and the densest 1-second packet
  window both at most 30 fps; audio optional and ignored; an embedded A119
  Novatek `freeGPS` track with at least one active fix. QuickTime (`qt  `
  brand) files are rejected.
- `metadata`: exactly `{"camera_utc_offset_minutes": <int>}`, the dashcam's
  time-zone setting as minutes from UTC (`-480` for UTC-08:00), an integer
  multiple of 15 from -720 through 840. `captured_at`, `latitude`, and
  `longitude` are not accepted for MP4: the job's values come from the first
  active GPS fix, with `captured_at` = camera time minus the offset. There is
  no fallback to `(0, 0)`, file times, filename times, or a fixed location.

```bash
curl --request POST http://localhost:8000/api/v1/inference/jobs \
  --form 'file=@20261120123000_000001.MP4;type=video/mp4' \
  --form 'metadata={"camera_utc_offset_minutes":-480}'
```

| Status | Code | Meaning |
| --- | --- | --- |
| 413 | `FILE_TOO_LARGE` | Request body or file over the 1,000,000,000-byte limit (with or without `Content-Length`) |
| 415 | `UNSUPPORTED_MEDIA_FORMAT` | Not MP4 bytes, QuickTime, H.265 or another codec, unsupported profile/pixel format, or more than one video stream |
| 422 | `INVALID_VIDEO` | Corrupt, truncated, or undecodable MP4 |
| 422 | `INVALID_VIDEO_DIMENSIONS` | Larger than 1920x1080 and not exactly 2560x1440 |
| 422 | `INVALID_VIDEO_FRAME_RATE` | Above 30 fps on average or in any 1-second window |
| 422 | `INVALID_VIDEO_DURATION` | Zero or over 600 s |
| 422 | `MISSING_GPS_TRACK` | No A119 GPS records, or no usable active fix (all void/invalid) |
| 422 | `INVALID_METADATA` | Missing or invalid `camera_utc_offset_minutes`, or image-only/unknown fields (`field` names the metadata key) |
| 422 | `EMPTY_FILE` | Zero-byte upload |
| 503 | `SUBMISSION_DEPENDENCY_UNAVAILABLE` | ffprobe/FFmpeg missing, object storage, database, or broker unavailable |

Validation runs off the event loop. The spooled upload is copied with a hard
byte cap to a private temporary file for `ffprobe` and the GPS parser; that
copy is always deleted, and the original spooled file is what gets streamed to
object storage. Tools only see local paths, run without a shell, with stdin
closed, the demuxer forced to MP4, only the `file` protocol allowed, timeouts
(`INFRASCAN_FFPROBE_TIMEOUT_SECONDS`, `INFRASCAN_FFPROBE_PACKET_TIMEOUT_SECONDS`,
`INFRASCAN_FFMPEG_TIMEOUT_SECONDS`), capped output, and an environment limited
to `PATH`/`LC_ALL`. `INFRASCAN_FFPROBE` / `INFRASCAN_FFMPEG` override the binaries.

### Object storage (Cloudflare R2) and submission persistence

New input and evidence media live in R2; PostgreSQL stores only the media ID,
canonical content type, original filename, object key, and media metadata.
Media is never sent through Celery or base64.

| Object | Key |
| --- | --- |
| JPEG input | `input/{media_id}.jpg` |
| MP4 input | `input/{media_id}.mp4` |
| Image evidence | `evidence/{job_id}/{media_id}.jpg` |
| Video frame evidence | `evidence/{job_id}/{timestamp_ms:08d}-{media_id}.jpg` |

Both the API and the worker need (placeholders only; never commit real values):

```bash
export R2_BUCKET="<bucket>"
export R2_ENDPOINT="https://<account_id>.r2.cloudflarestorage.com"
export R2_ACCESS_KEY_ID="<access_key_id>"
export R2_SECRET_ACCESS_KEY="<secret_access_key>"
```

If the R2 variables are missing, the API starts but submissions return
`503 SUBMISSION_DEPENDENCY_UNAVAILABLE`. MP4 input is streamed with boto3's
managed multipart transfer (16 MiB parts); JPEGs use a single `PutObject`.
Worker downloads go to a temporary file with a hard byte cap
(`INFRASCAN_WORK_DIR` optionally chooses its directory). Credentials, signed
URLs, object contents, and raw GPS tracks are never logged.

After validation, the service uploads the media, then in one transaction stores
`InputMedia` (content type `image/jpeg` or `video/mp4`, from validation rather
than the client, `data` NULL, `object_storage_key` set) and creates a `pending`
`InferenceJob` with `captured_at`, `latitude`, and `longitude`. Only after that
commit does it publish the Celery task `infrascan.process_inference_job`
whose single argument is the job ID string. `submitted_at` is the persisted
`created_at`. The request never waits for inference. Storage, database, and
broker calls run in a worker thread, off the event loop.

- If the upload fails (including a partial multipart upload), the key is
  deleted and the response is `503 SUBMISSION_DEPENDENCY_UNAVAILABLE`; nothing
  is inserted or enqueued.
- If the database is unreachable, the uploaded object is deleted and the
  response is `503 SUBMISSION_DEPENDENCY_UNAVAILABLE`; nothing is enqueued.
  Any other persistence error also deletes the object, rolls back both rows,
  and returns `500 INTERNAL_ERROR`.
- If publishing fails, the response is `503 SUBMISSION_DEPENDENCY_UNAVAILABLE`
  and the already-committed job is marked `failed` (best effort). Its input
  object is kept for diagnosis.
- Cleanup failures are logged with the object key and job ID and never hide
  the primary error.

Legacy rows whose `data` column still holds `BYTEA` bytes keep working: the
worker uses those bytes and `GET /api/v1/evidence/{media_id}` serves them
directly. R2-backed evidence is fetched after the database session closes; if
R2 cannot be read the endpoint returns `503 EVIDENCE_UNAVAILABLE`.

### Inference worker

The Celery task (`backend/inference/celery/tasks.py`) receives only the job ID
and uses `InferenceJobProcessor` (`backend/app/services/inference_processing.py`):

1. In a short transaction, it row-locks the job. If the job is still
   `pending`, it marks it `running` with `started_at` and reads the input image,
   then commits and closes the session. Unknown or non-pending jobs are logged
   and skipped.
2. It runs the v0-mvp pipeline with no session open
   (`inference_with_annotation` in `backend/inference/service.py`). Weights,
   confidence threshold, preprocessing, and the severity heuristic are the same
   as in `inference_service`. The annotated JPEG is encoded in memory for this
   invocation and is never read from the shared `data/outputs/annotated.jpg`.
3. It projects detection boxes onto a flat road using the sample camera profile
   and the job's capture GPS (see Prototype geolocation below). Projection runs
   with no database session open. Unprojectable detections retain NULL locations.
4. It uploads the annotated JPEG to R2, then in one new transaction saves one
   `EvidenceMedia` row (object key, no bytes), one `DetectionRecord` per
   detection (all pointing to that evidence row, with `observed_at` = the
   job's `captured_at`), links every detection to a canonical Issue (see
   [Issue consolidation](#issue-consolidation-detections-to-issues)), and
   sets `model_version` and `completed`/`completed_at`. A completed job
   therefore always has its saved output and Issue links; a failure anywhere
   in that transaction leaves none of them. An empty detection list is a
   successful job with zero detection rows and zero Issues (the annotated
   image is still stored).
5. If inference raises, its output is malformed, an evidence upload fails, or
   the result transaction fails, the error is logged with the job ID, every
   evidence object this attempt uploaded is deleted, and the job is marked
   `failed` when the database is reachable. `completed_at` stays NULL for
   failed jobs.

#### Video jobs

An MP4 is one application job with the same `pending -> running ->
completed|failed` lifecycle. No transaction or row lock is held while
downloading, decoding, inferring, or uploading.

1. Claim the job and mark it `running` in a short transaction.
2. Download the input from R2 into a private temporary directory with a
   1,000,000,000-byte cap (or write a legacy row's bytes).
3. Re-run `ffprobe` validation and parse the full GPS track; API-time checks
   are not trusted alone. The first fix must still match the job's stored
   coordinates.
4. Select at most 60 real presentation timestamps evenly across the clip
   (first and last included), decode exactly those frames in one bounded
   FFmpeg pass (`select` + `showinfo`), and verify the decoded PTS list and
   file count. The stored `frame_timestamp` is the actual PTS in seconds from
   the first frame, not a requested time.
5. Run the unchanged image model on each frame. A change of `model_version`
   within one clip fails the job.
6. Match each frame to GPS (below) and project its detections with that
   frame's base point and heading and the provisional A119 profile. Frames
   without a trustworthy fix are still inferred, but their detections get
   `location = null`; the job logs `frames_without_gps`.
7. Upload one annotated evidence JPEG per sampled frame (frames with zero
   detections included), then save all evidence rows, detections, and their
   Issue links with job completion in one transaction. Every video
   detection's `frame_timestamp` equals its evidence row's. Its
   `observed_at` is the frame's UTC time on the GPS-aligned timeline: the
   job's `captured_at` (the first fix, converted with the submitted camera
   offset) plus the frame's offset from that fix, so frames before lock or in
   an outage still get a time.
8. Delete all temporary files in every outcome. Failures delete this
   attempt's evidence objects and mark the job `failed`; no partial rows remain.

Sampling at most 60 frames means road damage visible only between sampled
frames can be missed; a 10-minute clip is sampled about every 10 s. Job
detections are ordered by `frame_timestamp` (NULL first, so images are
unchanged), then `detected_at`, confidence descending, and ID.

### A119 Mini 2 GPS metadata

**Manufacturer facts:** VIOFO documents the A119 Mini 2 as a single front
camera with MP4 output, built-in GPS logging, a 140° field of view, 1080p30
support, and loop lengths up to 10 minutes. GPS data is recorded when a signal
is available, and the camera time zone is a manual setting (including daylight
saving time).

**Independently observed binary behavior** (parser in
`backend/inference/telemetry/`): telemetry is proprietary Novatek data, not a
standard MP4 GPS track, so `ffprobe` cannot see it. The camera writes about one
`free` box per second whose payload starts with `GPS ` (`freeGPS `), and a
`moov/gps ` index (4-byte header, big-endian count, big-endian
`(offset, size)` pairs) pointing at them. Offsets are from the box size dword:

| Offset | Field |
| --- | --- |
| `0x00` | u32 BE box size |
| `0x04` | `freeGPS ` marker |
| `0x0C` | u32 LE record length, `0x2C` on the A119 Mini 2 (other values are rejected) |
| `0x10` | 6 × u32 LE hour, minute, second, year − 2000, month, day (camera clock) |
| `0x28` | status `A` (active) or `V` (void / no lock) |
| `0x29`, `0x2A` | hemisphere `N`/`S`, `E`/`W` |
| `0x2C` | f32 LE latitude, NMEA `DDMM.MMMM` |
| `0x30` | f32 LE longitude, NMEA `DDDMM.MMMM` |
| `0x34` | f32 LE speed, knots (× 1.852 = km/h) |
| `0x38` | f32 LE course, degrees clockwise from true north |

Sources pinned for these offsets: ExifTool 12.76 `QuickTimeStream.pl` GPSType
"1C" (tag commit `212f02cf22b9484674752181cad3b73089ce9694`; Artistic/GPL),
[ibragari/route_overlay](https://github.com/ibragari/route_overlay)
`lib/novatek_gps.rb` at `66f8448a68fb9a969205d00004b6d5bae04056f4` (MIT,
A119 Mini 2-tested), and
[everydashcam](https://github.com/everydashcam/everydashcam) `freegps.ts` at
`21af3a0494506e38d794f6e48eff36287b16dec4` (AGPL-3.0; consulted only, no code
or fixtures copied). The parser was also run locally against everydashcam's
anonymized, GPS-only (2 KB, no video) A119 Mini 2 cold-start sample, which is
not committed: 8 records, 4 void pre-lock, 2 active fixes, a 3-hour clock jump
at lock, and same-second records with differing values. Other
Novatek layouts (for example the A119 v3 record length `0x3F0`) are rejected
rather than guessed.

Parser rules: a structurally valid `gps ` index is used; if it is missing or
any entry is out of bounds or does not point at a marker, a chunked marker scan
(1 MiB reads, split markers found exactly once, at most 2,400 records) is used
instead. Void records are counted but never emitted. Impossible dates,
coordinates (minutes ≥ 60, out of range, `(0, 0)`), unknown status or
hemisphere bytes, and truncated blocks are rejected per record. Non-finite or
out-of-range speed/course become NULL.

**Time zone:** after lock, the A119 Mini 2 writes local wall time as if it were
UTC; before lock, void records carry the RTC in UTC. The submitted
`camera_utc_offset_minutes` converts fix times: UTC = camera time − offset.

**Design choices (awaiting real-hardware confirmation):**

- Record slot `k` sits about `k` seconds into the clip. Fixes keep their own
  timestamp spacing; a fix's video offset is `anchor + (t − t_first)` where
  `anchor` is the median of `slot − (t − t_first)`. Fixes far outside the
  clip (more than 2 s) are dropped.
- Pre-lock timestamps never anchor the clip. A jump of 10 minutes or more
  between the last void record and the first fix is reported as a cold-start
  clock jump. A backwards or > 600 s discontinuity between active fixes starts
  a new epoch; only the latest epoch is kept.
- Same-second repeats: a byte-identical stale repeat is dropped; a
  same-second record with different values is ambiguous, so the first wins
  (counted separately). Stationary fixes with advancing time are kept.
- Frame matching: linear interpolation between bracketing fixes at most 5 s
  apart (longitude across the antimeridian, heading circularly across 0°);
  otherwise the nearest fix within 1 s; otherwise no location. Long outages
  (tunnels) are never bridged. Missing course is derived from consecutive
  fixes at least 3 m apart.

### Provisional A119 Mini 2 camera profile

Video detections use `provisional-viofo-a119-mini2-1080p`
(`backend/inference/geolocation/camera_profiles.py`): 1920 × 1080, principal
point at the image center, and `fx = fy ≈ 401 px` derived from the published
140° diagonal field of view as if the lens were an ideal pinhole. The real lens
is a distorting wide-angle lens, so this is an approximation, not a
calibration. Mounting defaults are 1.3 m height, 10° pitch down, and a 60 m
range; override them with `INFRASCAN_A119_FX_PX`, `INFRASCAN_A119_FY_PX`,
`INFRASCAN_A119_MOUNT_HEIGHT_M`, `INFRASCAN_A119_PITCH_DOWN_DEG`, and
`INFRASCAN_A119_MAX_GROUND_DISTANCE_M`. Projected pothole coordinates are not
hardware-calibrated until real footage is measured.

2560 × 1440 (1440P) clips use the same profile. The projection scales the
intrinsics to the frame size (4/3 on both axes, since both modes are 16:9),
which is only correct if 1440P records the same field of view as 1080P with no
crop or digital zoom. That has not been checked on hardware, so 1440P
geolocation is provisional until the 1440P mode is calibrated. The `fx`/`fy`
overrides above are in 1920 × 1080 pixels for both modes. Detection runs at the
model's fixed input size in either mode; no transcoding is done.

### Synthetic A119 Mini 2-like seed clip

There is no real camera sample in the repository.
`backend/scripts/generate_a119_mini2_seed.py` deterministically builds a clip
labeled `synthetic-a119-mini2-like`: a generated road-like pattern encoded as
1920x1080 30 fps H.264 High `yuv420p` (optional silent AAC), one `freeGPS`
block per second interleaved in `mdat`, and a `moov/gps ` index placed before
the tracks (as Novatek cameras do, and where ExifTool looks for it). The route
starts at Katella Ave / Harbor Blvd in the Stanton/Anaheim area heading north
at 40 km/h. Each clip gets a JSON manifest (`<name>.MP4.json`) with the video
properties, every written record, and the expected first fix.

```bash
cd backend
python scripts/generate_a119_mini2_seed.py --output-dir /tmp/a119-seed --audio --validate-exiftool
python scripts/generate_a119_mini2_seed.py --output-dir /tmp/a119-seed --scenario cold-start
python scripts/inspect_a119_clip.py /tmp/a119-seed/20261120123000_000001.MP4 \
  --camera-utc-offset-minutes -480 --extract-dir /tmp/a119-frames
```

Scenarios: `standard`, `cold-start` (void pre-lock RTC records, then a clock
jump), `duplicate` (stale repeat), `short-gap`, `long-gap` (8 s outage),
`corrupt-block` (month 13), `broken-index` (scan fallback), `no-gps`,
`southeast` (Sydney, S/E signs), `north-crossing` (course turning from
356° through north), and `stationary` (a parked camera: constant position and
course, used to test repeated observations of one defect).
`--no-index` omits the index. `--validate-exiftool` is an optional developer
check that ExifTool's independent `-ee` decoder reads every active fix; the
generator's packing is written separately from the parser, and the committed
192-byte golden file `backend/tests/fixtures/a119_mini2_type3_golden.bin`
(assembled by hand) pins the layout for both.

`scripts/inspect_a119_clip.py` is a dry run: it applies the API validation and
the worker's sampling and GPS matching without contacting R2, PostgreSQL, or
the model, and prints a JSON summary (`--frames` adds per-frame positions;
`--extract-dir` decodes the sampled frames, which doubles as an FFmpeg smoke
test).

Limitations: synthetic video is not camera footage and shows no real damage;
encoder settings, brands, time base, GPS block padding, the index header word,
and unknown trailing fields (for example accelerometer data) only approximate
the camera; positions, speed, and course are simulated.

### Real-hardware validation checklist

When an actual A119 Mini 2 clip is available:

1. Copy the original file directly from the SD card. Do not re-export it
   through an editor or app that may strip proprietary GPS atoms.
2. Record firmware version, camera resolution/fps/codec, loop setting, GPS
   setting, time zone, HDR state, and whether a satellite lock was visible.
3. Compare parser output to the VIOFO player for start time, several
   coordinates, speed, and route direction
   (`python scripts/inspect_a119_clip.py <clip> --camera-utc-offset-minutes <offset> --frames`,
   and `exiftool -ee -n -G3 -GPSDateTime -GPSLatitude -GPSLongitude <clip>`).
4. Compare `ffprobe` properties to the accepted contract (the inspection
   output's `video` section, or `ffprobe -v error -show_streams -show_format <clip>`).
5. Run the dry-run inspection above, which does not upload or persist the
   file. Optionally run `INFRASCAN_A119_REAL_CLIP=<clip>
   INFRASCAN_A119_REAL_CLIP_OFFSET_MINUTES=<offset> python -m pytest
   tests/test_a119_telemetry.py -k real`.
6. Anonymize or exclude private route data before committing any fixture.
7. Add a real-sample-derived regression fixture only after confirming
   license/privacy and stripping location identity.

The original upload stays linked to the job. The Celery task ID is separate
from the application job ID and is not stored. Task return values are ignored
because results are stored in the application tables. The worker creates its
engine lazily in each process and discards any inherited pool after a prefork
fork. The existing `POST /api/v1/inference/smoke-test` endpoint and
`infrascan.inference` task are unchanged.

### Job tracking

`GET /api/v1/jobs/{job_id}` returns the existing `InferenceJobDTO` fields from
PostgreSQL (`submitted_at` from `created_at`, capture metadata under `metadata`,
and an additional `model_version` field). `GET /api/v1/jobs/{job_id}/detections`
keeps the `page`/`page_size` pagination and returns an empty `items` list for
completed jobs with no detections. Both return `404` with
`{"error":{"code":"JOB_NOT_FOUND",...}}` for unknown jobs. Each detection keeps
the existing fields (`classification` is derived from the stored class ID;
`latitude`/`longitude` mirror the projected `location`, or are NULL) and adds
`issue_id` (the linked Issue), `issue_type`, `evidence_media`, `location`,
`bounding_box`, `frame_timestamp`, and `observed_at` (capture time of the
image or frame; `detected_at` remains the time inference finished). The
Issues a job created or updated are `GET /api/v1/issues?job_id=<job_id>`
(the same filter also works on `/issues/map`).

### Issue consolidation (detections to Issues)

Every detection with a mapped model class is assigned to exactly one
canonical Issue inside the worker's result transaction
(`backend/app/services/issue_linking.py`). Detections are taken frame by frame
in capture order and, within a frame, most confident first.

**Matching rule.** A located detection joins the closest *open* Issue
(`pending_review` or `accepted`) of the **same canonical type** within the
match radius, measured as great-circle distance in meters from the Issue's
point. Issues that already hold a detection from the same image or video
frame are skipped, so distinct boxes in one frame never collapse, however
close their projections are. Ties go to the earlier `first_seen_at`, then the
lower `issue_id`. With no match, a new Issue is created at the detection's
point. Candidates come from a bounding box around the point that uses the
existing partial GiST index `ix_issues_location` (split at the antimeridian),
then exact distance is checked; there is no degree arithmetic and no
whole-job grouping.

| Setting | Default | Meaning |
| --- | --- | --- |
| `INFRASCAN_ISSUE_MATCH_RADIUS_M` | `8` | Match radius in meters, `(0, 100]` |
| `INFRASCAN_ISSUE_LOW_CONFIDENCE` | `0.5` | Below this, an Issue's best detection flags `low_confidence` |

The 8 m default reflects the current prototype, not a measured accuracy: a
consumer GPS fix is typically several meters off, two independent passes
differ by more, and the uncalibrated flat-road projection adds error that
grows with distance. Radius 8 m errs toward duplicate Issues rather than
merging separate defects. Distinct same-type defects closer than 8 m seen in
*different* frames or submissions can still merge, and observations of one
defect projected more than 8 m apart (for example from far away, or a JPEG
whose real heading is not north) stay separate.

**Other cases.**

- *Different types* never merge. A new Issue created within the radius of an
  open Issue of another type is flagged `type_conflict`.
- *Unlocated detections* (no projectable point) are still saved and linked to
  an unlocated Issue (`missing_location`) that appears in the review queue
  and never on the map. They never match across submissions. Within one video
  job, an unlocated detection continues the unlocated Issue of the
  immediately preceding sampled frame when the type matches and the boxes
  overlap by IoU of at least 0.3; otherwise it starts a new one.
- *Unmapped model classes* (not in `MODEL_CLASS_ISSUE_TYPES`) are saved with
  `issue_id` NULL and logged with the job ID; no Issue type is invented.
- *Low confidence:* low-confidence detections still link. An Issue whose best
  detection is below the threshold carries `low_confidence` until a more
  confident detection arrives.
- *Reviewed Issues:* `resolved` and `dismissed` Issues are never matched or
  changed; a new detection there starts a new Issue for review. `accepted`
  Issues gain the detection and wider seen times only; severity, location,
  status, and review reason are reviewer-owned.

**Issue fields.** Worker-created Issues start `pending_review`; nothing in the
pipeline marks an Issue accepted. While pending, the location is the mean of
the located supporting detections, severity is the most severe detection, and
the review reason is `missing_location`, else `type_conflict` (sticky), else
`low_confidence`, else none. `first_seen_at`/`last_seen_at` are the min/max
`observed_at` of the supporting detections (image: the job's `captured_at`;
video: the frame's GPS-timeline time), never the worker clock.
`location_accuracy_m` stays NULL because the projection is uncalibrated.

**Concurrency and retries.** Matching runs under a transaction-scoped
PostgreSQL advisory lock (`pg_advisory_xact_lock`) taken only in the short
result transaction, after download, decoding, inference, and R2 uploads, so
two jobs for the same defect cannot both miss each other's new Issue.
Candidate Issue rows are also locked `FOR UPDATE`. A redelivered task for a
job that is no longer `pending` is skipped and changes nothing.

#### Backfilling historical detections

Detections saved before this feature have `issue_id` NULL. After
`alembic upgrade head` (which also fills `observed_at` for still-image rows),
run the backfill from `backend/`. It replays the same rule, in capture order,
under the same lock:

```bash
python -m app.maintenance.backfill_issue_links           # dry run: report, then roll back
python -m app.maintenance.backfill_issue_links --apply   # commit
```

It only considers rows with `issue_id IS NULL`, so rerunning it links nothing
new. Rows it cannot link are listed by detection ID and left unchanged:
`unmapped_class`, and `unknown_observation_time` for historical *video*
detections, whose frame times depend on a GPS track that PostgreSQL does not
store. Unlocated historical detections are linked to review-queue Issues. Do
not use the Stanton–Anaheim seed as a substitute: it only inserts
demonstration rows. (The seed now derives its Issue seen times from
`observed_at`; re-run it after upgrading if its rows are present.)

### Issue map and upload flow (frontend)

After a job completes, the upload page (`#/jobs/<job_id>`, so it survives
navigation) lists the job's linked Issues from `GET /api/v1/issues?job_id=`
with links to each Issue, a **View on issue map** link that frames the
job's located Issues (`#/map?bounds=south,west,north,east&issue=<id>`) and
preselects one, and the review queue when some Issues have no location. Each
detection row links to its Issue. The map always fetches
`/api/v1/issues/map` for the current viewport when it opens (with
`cache: "no-store"`); a **Refresh** button refetches an open map. Issue
detail pages show supporting detections with their observation time and
evidence, and a **Show on map** link for located Issues.

### Prototype geolocation

The persisted job pipeline now calculates sample detection GPS in
`InferenceJobProcessor.process`, immediately after YOLO and before saving results.
The standalone model/smoke-test functions still return model output only, because
they do not receive capture GPS. No migration or submission API change is needed;
`GET /api/v1/jobs/{job_id}/detections` exposes the existing location fields.

Edit `backend/inference/geolocation/camera_profiles.py` to configure the test:

- `SAMPLE_CAMERA_PROFILE`: synthetic 4032 × 3024 calibration, `fx=fy=3000` pixels,
  principal point `(2016, 1512)`, camera height `1.5` meters above the road,
  pitch `15` degrees downward, maximum ground distance `100` meters.
- `PROTOTYPE_HEADING_DEG`: fixed heading clockwise from true north, currently
  `0` (north). All jobs use this assumption until per-capture heading is added.
  This is unrelated to the EXIF Orientation rotation flag.

These values are **illustrative, not a measured calibration of any device**.
Restart workers after editing them. For tests or a later lookup, the processor
also accepts `camera_profile=` and `heading_deg=` arguments. The chosen profile
name and heading are logged; a calibration snapshot is not persisted yet.

`projection.py` uses each box's **bottom center** as a ground contact estimate.
It scales the pixel intrinsics to YOLO's original prediction-source dimensions,
intersects the camera ray with a flat road, rotates the right/forward offset by
heading, and moves from the job's capture GPS on a spherical Earth. Intrinsics
use the [OpenCV pinhole convention](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).
The source image is made upright using EXIF Orientation before inference, so
boxes, annotated images and the calibration all use that coordinate frame.

The prototype assumes zero roll, zero lens distortion, and full-frame resizing;
it does not infer crops, digital zoom, camera identity, or pose from metadata.
An invalid box, ray at/above the horizon, intersection behind the camera, or
distance over the configured limit produces a NULL location while preserving
the detection and completing the job. Missing/invalid prediction dimensions
fail the job because its coordinate frame is unknown. Empty detections still
complete normally. Sample GPS values demonstrate the pipeline; accuracy testing
requires matching measured intrinsics, mounting height, pitch and heading.

### Deferred limitations (post-MVP)

- No retries. A failed job stays `failed`.
- No transactional outbox. If the process dies between the commit and the
  publish, the job stays `pending` and nothing republishes it.
- Limited duplicate-delivery guarantees. The row lock and `pending` check
  stop a redelivered message from reprocessing a job that has already been
  claimed or completed, so it adds no detections and changes no Issue, but
  there is no general idempotent-delivery mechanism.
- No recovery for stranded jobs. Jobs left `pending` or `running` after a crash
  (for example, a worker killed during inference; `task_acks_late` causes
  redelivery, and the redelivered message is skipped because the job is
  `running`) need manual handling.
- Failed jobs do not store an error message, so `error` in the job DTO stays
  NULL.
- Image detection GPS uses the synthetic prototype profile above. Device
  lookup, EXIF GPS parsing and per-image pose remain deferred.
  `frame_timestamp` is NULL for still images.
- An MP4 upload temporarily occupies up to twice its size on the API host's
  temporary disk (Starlette's spooled upload plus the validation copy), and the
  worker needs its size again plus up to 60 decoded frames.
- Issue matching serializes result saves across all workers with one
  advisory lock. That is fine at prototype volume; a busy deployment would
  need finer-grained (for example, geographic-cell) locks.
- Issue positions inherit the prototype projection's errors (uncalibrated
  intrinsics, assumed mounting, flat road, north-facing JPEGs). No accuracy
  is stored, and the match radius cannot correct a wrong heading.
- There is no reviewer workflow yet (`PATCH /issues/{id}` returns 501), so
  `accepted`/`resolved`/`dismissed` can only be set directly in the database.

`UploadLimitMiddleware` rejects submission bodies over 1,000,000,000 bytes plus
64 KiB of multipart overhead early, from `Content-Length` when present and
while streaming otherwise, with the same `413 FILE_TOO_LARGE` envelope.
Production deployments should still configure matching limits at the reverse
proxy.

### Database persistence models and migrations

SQLAlchemy 2.x ORM models live under `backend/app/models/` and share the
declarative `Base` in `backend/app/models/base.py`:

- `InputMedia` (`input_media`): stores `media_id` (UUID primary key),
  `object_storage_key` (R2 key, nullable), `data` (nullable legacy `BYTEA`
  payload; new rows leave it NULL), `content_type` (`image/jpeg` or
  `video/mp4`), and `filename` (nullable string). A check constraint requires
  `data` or `object_storage_key`.
- `InferenceJob` (`inference_jobs`): stores `job_id` (UUID primary key),
  `user_id` (nullable UUID without a foreign key until authentication and a
  `users` table exist), `input_media_id` (required foreign key to
  `input_media.media_id` with `ON DELETE RESTRICT`, indexed and non-unique so
  multiple jobs may reference the same media), `status` (`pending`, `running`,
  `completed`, or `failed`, defaulting to `pending`), `model_version` (nullable
  string with no default until assigned by the service/worker), `created_at`
  (required `TIMESTAMPTZ` defaulting to database `now()`), `started_at` and
  `completed_at` (nullable `TIMESTAMPTZ`), `captured_at` (required
  `TIMESTAMPTZ`), `latitude` (required `NUMERIC` in `[-90, 90]`), and
  `longitude` (required `NUMERIC` in `[-180, 180]`).
- `EvidenceMedia` (`evidence_media`): stores the annotated evidence JPEG,
  separate from the input. Columns are `media_id` (UUID primary key), `job_id`
  (required, indexed foreign key to `inference_jobs`), `object_storage_key`,
  `data` (nullable legacy `BYTEA`; at least one of the two is required),
  `content_type`, `filename` (nullable), and `frame_timestamp` (nullable,
  nonnegative seconds; NULL for image evidence, the source frame PTS for video
  evidence). All detections from one processed image or video frame share one
  evidence row.
- `DetectionRecord` (`detection_records`): one row per detection. Columns are
  `detection_id` (UUID primary key); `job_id` (required, indexed, non-unique
  foreign key to `inference_jobs`, so one job has zero or more detections);
  `issue_id` (nullable foreign key to `issues`; set by the worker, NULL only
  for unmapped model classes and historical rows not yet backfilled); and
  `issue_type` (required integer).
  `evidence_media` is a nullable foreign key to `evidence_media.media_id`.
  `location` is nullable `JSONB` (`{"latitude": ..., "longitude": ...}`, NULL
  when projection is unavailable). `confidence` is a required double in `[0, 1]`. `severity` is
  required and must be `minor`, `moderate`, or `critical`. `bounding_box` is a
  required `JSONB` array of four numbers. `frame_timestamp` is a nullable
  nonnegative offset in seconds, NULL for still images and equal to its
  evidence row's `frame_timestamp` for video. `detected_at` is a
  required `TIMESTAMPTZ` that the worker sets in UTC when inference finishes.
  `observed_at` is a nullable `TIMESTAMPTZ`: when the source image or frame
  was captured (NULL only for historical video rows).
  All of the new foreign keys use `ON DELETE RESTRICT`.

**Raw `issue_type`:** `issue_type` stores the v0-mvp model class ID as
predicted (`0` Longitudinal crack, `1` Transverse crack, `2` Alligator crack,
`3` Pothole; labels in `backend/inference/labels.py`). Revision
`4b7d2e9c5a18` created `issues` and `issue_types` (with each type's
`model_class_id`) and the `issue_id` foreign key; `MODEL_CLASS_ISSUE_TYPES`
in `backend/app/models/issue.py` maps class IDs to canonical types.
`issue_type` deliberately has no foreign key, so a detection's own prediction
stays visible even when it differs from its Issue's type.

**Bounding boxes:** `bounding_box` is `[x1, y1, x2, y2]` in fractional pixels of
the image the model received, at its original dimensions. It is not a
normalized YOLO training coordinate and is not on a fixed 640×640 grid. The
pipeline does not apply EXIF orientation, so coordinates refer to the stored
pixel grid.

**API timestamp mapping (`created_at` <-> `submitted_at`):** The public HTTP
contract (`InferenceJobAccepted`, `InferenceJobDTO`, and the `submitted_after` /
`submitted_before` query filters) uses `submitted_at`, whereas the ORM model and
`inference_jobs` table persist `created_at`. Future service and repository
implementations must map `InferenceJob.created_at` to `submitted_at` (and query
`created_at` for submission-time filters) so the external API contract remains
unchanged.

Schema lifecycle is managed exclusively with Alembic (`backend/alembic.ini` and
`backend/alembic/`), never with startup `Base.metadata.create_all()`.
Application tables are kept separate from Celery's result-backend tables
(`celery_taskmeta` and `celery_tasksetmeta`), which `backend/alembic/env.py`
excludes from autogenerate comparisons.

#### Supplying `DATABASE_URL`

Database credentials are never hardcoded in `alembic.ini` or source files.
Supply a PostgreSQL connection string in the `DATABASE_URL` environment
variable (`postgresql://`, `postgres://`, or `postgresql+psycopg://`); Alembic
automatically normalizes `postgresql://` and `postgres://` URLs to use the
installed `psycopg` (v3) driver:

```bash
export DATABASE_URL="postgresql://<user>:<password>@<host>:5432/<dbname>"
```

#### Common Alembic commands

Run from `backend/` (or pass `-c backend/alembic.ini` from the repository root):

```bash
cd backend

# Render migration SQL offline without connecting to a database
alembic upgrade head --sql

# Inspect the current revision applied to DATABASE_URL
alembic current

# Apply all pending migrations
alembic upgrade head

# Roll back one revision (or use `base` to revert all revisions in this env)
alembic downgrade -1

# Generate a new revision from ORM metadata changes
alembic revision --autogenerate -m "describe schema change"
```

When running against the Docker Compose stack, execute Alembic inside a one-off
`api` container (which already receives `DATABASE_URL` from `compose.yaml`):

```bash
docker compose run --rm api alembic current
docker compose run --rm api alembic upgrade head
```

#### Reviewing generated migrations

`alembic revision --autogenerate` only produces a candidate diff against the
connected database and **must always be reviewed and edited before committing or
applying**:

- Verify table and foreign-key creation/drop ordering in both `upgrade()` and
  `downgrade()`.
- Confirm `CHECK` constraints, `ON DELETE` rules, indexes, nullability, and
  `server_default` expressions match intended PostgreSQL semantics (autogenerate
  does not reliably detect arbitrary changes to existing `CHECK` expressions).
- Check for unintended destructive operations (table/column drops instead of
  renames) or any accidental changes to non-application objects.
- Add explicit data backfills when introducing non-nullable columns on populated
  tables, and ensure `downgrade()` removes only objects created by that
  revision.

#### Migration history

| Revision | Change |
| --- | --- |
| `7c4e9a1b2d01` | Creates `input_media`, `inference_jobs` |
| `9e2b6d4f1a37` | Creates `evidence_media`, `detection_records` |
| `4b7d2e9c5a18` | Creates `issue_types`, `issues`; links `detection_records.issue_id` |
| `a1c3e5f70829` | Adds nullable `object_storage_key` to both media tables |
| `b2d4f6a81930` | Makes media `data` nullable and requires `data` or `object_storage_key` |
| `c3e5f7a92041` | Adds nonnegative `evidence_media.frame_timestamp` |
| `d4f6a8b03152` (head) | Adds `detection_records.observed_at`; fills it from `captured_at` for still-image rows |

All three media revisions preserve existing `BYTEA` rows. Downgrading
`b2d4f6a81930` refuses to run while R2-only media rows exist, because their
bytes are not in PostgreSQL; move or delete those rows first.

### Running the inference workflow

The API and the worker both need `DATABASE_URL`, `CELERY_BROKER_URL`, and the
R2 variables above, plus `ffprobe`/`ffmpeg` on `PATH` (both Dockerfiles install
FFmpeg). The schema must be at `alembic upgrade head` before the first
submission.

With Docker Compose:

```bash
docker compose up -d db rabbitmq
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m app.maintenance.backfill_issue_links --apply  # once, for older detections
docker compose up --build api worker
```

Without Docker (PostgreSQL and RabbitMQ already running; the worker
environment additionally needs `ultralytics`/`torch`, as in
`backend/inference/Dockerfile`):

```bash
cd backend
export DATABASE_URL="postgresql://infrascan:infrascan_dev@localhost:5432/infrascan"
export CELERY_BROKER_URL="amqp://infrascan:infrascan_dev@localhost:5672//"
alembic upgrade head
python -m app.maintenance.backfill_issue_links --apply  # once, for older detections
uvicorn main:app --reload                               # API
celery -A inference.celery worker --loglevel=info       # worker, separate shell
```

Submit an image, then poll the returned application job ID:

```bash
curl --request POST http://localhost:8000/api/v1/inference/jobs \
  --form 'file=@road.jpg;type=image/jpeg' \
  --form 'metadata={"captured_at":"2026-11-10T14:30:00-08:00","latitude":33.8823,"longitude":-117.8851}'
curl http://localhost:8000/api/v1/jobs/<job_id>
curl "http://localhost:8000/api/v1/jobs/<job_id>/detections?page=1&page_size=50"
curl "http://localhost:8000/api/v1/issues?job_id=<job_id>"          # the job's Issues
curl "http://localhost:8000/api/v1/issues/map?min_latitude=33.87&min_longitude=-117.90&max_latitude=33.90&max_longitude=-117.87"
```

In the browser (`npm run dev` in `frontend/InfraScan frontend`), the same flow
is: upload a JPEG (time, latitude, longitude) or an A119 Mini 2 MP4 (camera
time zone only), wait for **completed**, then use **View on issue map** or a
row's **Open issue** link. Located Issues appear as markers in the framed
viewport; unlocated ones are listed under **Review queue**.

The model CLI smoke check now runs as a module from `backend/` because
`service.py` imports `inference.labels`:
`python -m inference.service`.

### Backend tests

Run from `backend/`:

```bash
python -m pytest tests
```

PostgreSQL-backed tests start a disposable local cluster (PostgreSQL 16
`initdb`/`pg_ctl`; they are skipped if those binaries are not installed) and
build the schema with `alembic upgrade head`. Most workflow tests mock the model
with `tests/fixtures/v0_mvp_multi_png_output.json`, which is real v0-mvp output
on `inference/data/uploads/multi.png`. `tests/test_real_model_pipeline.py` runs
the shipped weights. It covers HTTP submission, a Celery worker on an in-memory
broker, YOLO, PostgreSQL, and the tracking endpoints. It runs only when
`ultralytics` is importable and is skipped otherwise.

Storage is always an in-memory fake; no test contacts R2 or needs credentials.
Video tests generate small synthetic seed clips on the fly and are skipped
without `ffmpeg`/`ffprobe`; the ExifTool cross-check is skipped without
`exiftool`. To exercise a real camera clip without committing it, set
`INFRASCAN_A119_REAL_CLIP=<path>` and
`INFRASCAN_A119_REAL_CLIP_OFFSET_MINUTES=<offset>`.

| File | Covers |
| --- | --- |
| `tests/test_a119_telemetry.py` | GPS block golden offsets, index/scan discovery, corrupt and void records, time zones, track alignment, frame matching |
| `tests/test_video_pipeline.py` | `ffprobe` contract limits, bounded subprocesses, sampling, frame extraction, seed round-trips |
| `tests/test_video_submission.py` | MP4 API contract, byte-based detection, every MP4 error code, 413 middleware, OpenAPI |
| `tests/test_video_workflow.py` | R2 streaming and cleanup, video worker, evidence API, migrations, end-to-end clip processing through Issues, map, detail, and review queue |
| `tests/test_inference_workflow.py` | JPEG submission, worker persistence, Issue links, and the job → Issue → map/detail/evidence path |
| `tests/test_issue_consolidation.py` | Matching rule (radius in meters, ties, types, statuses, low confidence, unlocated, unmapped), rollback, duplicate delivery, concurrent jobs, advisory lock, index use, backfill, `observed_at` migration |
| `tests/test_issue_map.py` | Issue map/list/detail/review APIs and the Stanton–Anaheim seed |
| `tests/test_r2_storage.py` | R2 adapter transfer settings and download caps with a fake S3 client |

## Possible Model Evaluation Metric for Model
- mAP50-95
- Mean IoU
- Recall per Damange class
- Geolocation Mean Error

## Model (YOLO11)
size | param | mAP50-95 | CPU Inf.
---------------------------------
n    | 2.6M  | 39.5%    | 56ms

s    | 9.4M  | 47%      | 90ms

m    | 20.1M | 51.5%    | 183ms

L    | 25.3M | 53.4%    | 239ms

For local frontend development, the API allows cross-origin requests from
`http://localhost:5173`.

## Local Docker development

Copy `.env.example` to `.env`, then start the API and PostgreSQL:

```bash
cp .env.example .env
docker compose up --build
```

The API is available at `http://localhost:8000` and its interactive schema at
`http://localhost:8000/docs`. PostgreSQL is available on port `5432`; the API
container receives its connection string in `DATABASE_URL` and waits for the
database health check before starting. Apply migrations before the first
submission (see [Running the inference workflow](#running-the-inference-workflow)).
The example database credentials are for local development only; change them in
`.env` before using this setup elsewhere.

Stop the containers with `docker compose down`. To also remove the local
database volume, run `docker compose down --volumes`.

## Initial Supported Devices/Media File Format for Happy-Path Development
- JPEG -> Image file should contain GPS coordinates and specific camera specs (will specify soon)
- MP4 -> VIOFO A119 Mini 2 at 1080p30 or 1440p30 H.264 with GPS on (see
  [the MP4 contract](#viofo-a119-mini-2-mp4-submission-contract)); parsing is
  validated against synthetic clips and a public sample only, not yet against
  a clip recorded for this project
- pick top 2-3 most popular dashcams in United States and India (1 second will have some number of frames with geo-tagged lat/long & gps trajectory)

- after this, we either need to expand to support more devices with different formats and/or allow users to geo-tag issues after detections

## Current state of Geolocation method
- method: inverse projection mapping
- constraints: each frame will have have corresponding location
- high level:
	- detection_location = camera_or_source_gps + offset_detection_from_camera/source(thru inverse projection mapping)

- current state:
	- prototype have worked
    - sample camera profile and flat-road projection are wired into the image job worker
    - need extensive testing though
    - Need to find top 2-3 most popular dashcams from India and America and provide support
       - for now, just pick one from each side and provide meta data parsing and extraction 
