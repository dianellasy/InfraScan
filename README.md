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
| `GET` | `/api/v1/media/evidence/{media_id}` |
| `GET` | `/api/v1/media/inputs/{media_id}` |
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
`GET /api/v1/jobs/{job_id}/detections`,
`GET /api/v1/detections/{detection_id}/evidence`, and the two
`GET /api/v1/media/...` image endpoints; the other endpoints return
`501 NOT_IMPLEMENTED`. Without `DATABASE_URL`, no service is attached and API
calls return `503 Service Unavailable` rather than placeholder data.

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
`INTERNAL_ERROR`.

Authentication is intentionally not enforced in this milestone. The future
production request will carry `Authorization: Bearer <token>` and the backend
will derive the authenticated user ID; client-supplied user IDs are rejected as
unknown metadata.

After validation, the application service reads the upload and, in one
transaction, stores it as `InputMedia` (content type `image/jpeg`, taken from
validation rather than the client) and creates a `pending` `InferenceJob` with
`captured_at`, `latitude`, and `longitude`. Only after that commit does it
publish the Celery task `infrascan.process_inference_job` whose single argument
is the job ID string. `submitted_at` is the persisted `created_at`. The request
never waits for inference. Database and broker calls run in a worker thread,
off the event loop, each with its own short-lived session.

- If the database is unreachable, the response is
  `503 SUBMISSION_DEPENDENCY_UNAVAILABLE` and nothing is enqueued. Any other
  persistence error rolls back both rows and returns `500 INTERNAL_ERROR`.
- If publishing fails, the response is `503 SUBMISSION_DEPENDENCY_UNAVAILABLE`
  and the already-committed job is marked `failed` (best effort).

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
3. In one new transaction, it saves one `EvidenceMedia` row, one
   `DetectionRecord` per detection (all pointing to that evidence row, with its
   geolocation result when the job carries POC provenance),
   `model_version`, and `completed`/`completed_at`. A completed job therefore
   always has its saved output. An empty detection list is a successful job with
   zero detection rows (the annotated image is still stored).
4. If inference raises, its output is malformed, or the result transaction
   fails, the error is logged with the job ID and the job is marked `failed`
   when the database is reachable. `completed_at` stays NULL for failed jobs.

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
`latitude`/`longitude` mirror `location` and are NULL when no location was
computed) and adds `issue_id`, `issue_type`, `evidence_media`, `location`,
`bounding_box`, and `frame_timestamp`.

Image and geolocation fields (absolute URLs are built from the request's host):

| Response | Field | Meaning |
| --- | --- | --- |
| Job | `annotated_media_id`, `annotated_image_url` | The job's own annotated JPEG; set once the job completes, including when it has zero detections |
| Job | `geolocation` | Provenance of the coordinates: `mode` (`poc_assumed`), `assumed`, `note`, `source` (name, latitude, longitude, datum), `camera` (profile, height, orientation, limits), `request_gps` (what the client sent, `used: false`), `image` (oriented width/height), `intrinsics` (fx, fy, cx, cy at that size). NULL for jobs submitted without POC geolocation |
| Detection | `evidence_media_url` | URL of the annotated JPEG the detection belongs to |
| Detection | `location` | `{"latitude", "longitude"}` WGS84 demo estimate, or NULL |
| Detection | `location_status` | `estimated` when `location` is set; otherwise why it is NULL (see below) |
| Detection | `location_details` | `method`, `basis`, `contact_pixel` `[u, v]`, `east_m`, `north_m`, `ground_distance_m`, and a `message` when no location was computed |

`GET /api/v1/media/evidence/{media_id}` and `GET /api/v1/media/inputs/{media_id}`
return the stored image bytes with their stored `Content-Type` (`image/jpeg`),
`Content-Disposition: inline`, and a private immutable cache header, so an
`<img src>` can use the URLs directly. Unknown IDs return `404 MEDIA_NOT_FOUND`.
The bytes always come from the database row for that job; no filesystem path
is returned and the shared `inference/data/outputs/annotated.jpg` is never read.
`GET /api/v1/detections/{detection_id}/evidence` returns one frame with
`original_url`, `annotated_url`, and the bounding box, or
`404 DETECTION_NOT_FOUND`. Like the rest of the API, these endpoints are not
authenticated yet.

### Deferred limitations (post-MVP)

- No retries. A failed job stays `failed`.
- No transactional outbox. If the process dies between the commit and the
  publish, the job stays `pending` and nothing republishes it.
- No duplicate-delivery guarantees. The row lock and `pending` check stop a
  redelivered message from reprocessing a job that has already been claimed,
  but this is not idempotent delivery.
- No recovery for stranded jobs. Jobs left `pending` or `running` after a crash
  (for example, a worker killed during inference; `task_acks_late` causes
  redelivery, and the redelivered message is skipped because the job is
  `running`) need manual handling.
- Failed jobs do not store an error message, so `error` in the job DTO stays
  NULL.
- Detection GPS (`location`) is calculated only in the
  [geolocation proof of concept](#geolocation-proof-of-concept), from an
  assumed source location and camera setup. Without it, `location` is NULL.
  `frame_timestamp` is NULL for still images.

The controller bounds reads of the uploaded file, but this is not a
server-wide request-body limit. Production deployment must separately
configure limits at the reverse proxy/ASGI server and account for multipart
parser limits.

### Database persistence models and migrations

SQLAlchemy 2.x ORM models live under `backend/app/models/` and share the
declarative `Base` in `backend/app/models/base.py`:

- `InputMedia` (`input_media`): stores `media_id` (UUID primary key), `data`
  (required PostgreSQL `BYTEA` payload for JPEG bytes), `content_type`
  (required string), and `filename` (nullable string).
- `InferenceJob` (`inference_jobs`): stores `job_id` (UUID primary key),
  `user_id` (nullable UUID without a foreign key until authentication and a
  `users` table exist), `input_media_id` (required foreign key to
  `input_media.media_id` with `ON DELETE RESTRICT`, indexed and non-unique so
  multiple jobs may reference the same media), `status` (`pending`, `running`,
  `completed`, or `failed`, defaulting to `pending`), `model_version` (nullable
  string with no default until assigned by the service/worker), `created_at`
  (required `TIMESTAMPTZ` defaulting to database `now()`), `started_at` and
  `completed_at` (nullable `TIMESTAMPTZ`), `captured_at` (required
  `TIMESTAMPTZ`), `latitude` (required `NUMERIC` in `[-90, 90]`),
  `longitude` (required `NUMERIC` in `[-180, 180]`), and `geolocation`
  (nullable `JSONB` provenance; see the job `geolocation` field above). In POC
  mode `latitude`/`longitude` hold the effective (assumed) source GPS, and the
  client's values are kept in `geolocation.request_gps`.
- `EvidenceMedia` (`evidence_media`): stores the annotated evidence JPEG,
  separate from the input. Columns are `media_id` (UUID primary key), `job_id`
  (required, indexed foreign key to `inference_jobs`), `data` (`BYTEA`),
  `content_type`, and `filename` (nullable). All detections from one processed
  image share one evidence row.
- `DetectionRecord` (`detection_records`): one row per detection. Columns are
  `detection_id` (UUID primary key); `job_id` (required, indexed, non-unique
  foreign key to `inference_jobs`, so one job has zero or more detections);
  `issue_id` (nullable UUID, NULL for now); and `issue_type` (required integer).
  `evidence_media` is a nullable foreign key to `evidence_media.media_id`.
  `location` is nullable `JSONB` (`{"latitude": ..., "longitude": ...}`).
  `location_status` is a nullable string restricted to the projection statuses
  (`estimated`, `above_horizon`, `behind_camera`, `unstable_near_horizon`,
  `outside_image`, `nonfinite`, `undistortion_failed`, `intrinsics_mismatch`),
  and `location_details` is nullable `JSONB`. All three are NULL when no
  geolocation was attempted. `confidence` is a required double in `[0, 1]`. `severity` is
  required and must be `minor`, `moderate`, or `critical`. `bounding_box` is a
  required `JSONB` array of four numbers. `frame_timestamp` is a nullable
  nonnegative offset in seconds, NULL for still images. `detected_at` is a
  required `TIMESTAMPTZ` that the worker sets in UTC when inference finishes.
  All of the new foreign keys use `ON DELETE RESTRICT`.

**Provisional `issue_type`:** For now, `issue_type` stores the v0-mvp model
class ID (`0` Longitudinal crack, `1` Transverse crack, `2` Alligator crack,
`3` Pothole). That mapping is defined once in `backend/inference/labels.py`.
These are model class IDs, not permanent domain identifiers. The intended
foreign keys from `issue_id` and `issue_type` to `issues` and `issue_types` are
deferred because those tables do not exist yet. No placeholder tables or
references are declared. A later issue-types migration must define the mapping
from class IDs and convert stored values.

**Bounding boxes:** `bounding_box` is `[x1, y1, x2, y2]` in fractional pixels of
the upright image, at its original dimensions. It is not a normalized YOLO
training coordinate and is not on a fixed 640×640 grid. The pipeline applies
the JPEG's EXIF orientation before inference, so boxes, the annotated image,
and the geolocation intrinsics all use the same oriented width and height. For
EXIF-rotated uploads this grid differs from the raw stored pixel grid.

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

| Revision | Creates |
| --- | --- |
| `7c4e9a1b2d01` | `input_media`, `inference_jobs` |
| `9e2b6d4f1a37` | `evidence_media`, `detection_records` |
| `c3f8a2d6e915` (head) | `inference_jobs.geolocation`, `detection_records.location_status` (with check), `detection_records.location_details` |

### Running the inference workflow

The API and the worker both need `DATABASE_URL` and `CELERY_BROKER_URL`. The
schema must be at `alembic upgrade head` before the first submission.

With Docker Compose:

```bash
docker compose up -d db rabbitmq
docker compose run --rm api alembic upgrade head
docker compose up --build api worker
```

Without Docker (PostgreSQL and RabbitMQ already running; the worker
environment additionally needs `ultralytics`/`torch`, as in
`backend/inference/Dockerfile`):

```bash
cd backend
export DATABASE_URL="postgresql://infrascan:infrascan_dev@localhost:5432/infrascan"
export CELERY_BROKER_URL="amqp://infrascan:infrascan_dev@localhost:5672//"
export INFRASCAN_POC_GEOLOCATION=1                      # optional demo mode, read by the API
alembic upgrade head
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
```

The model CLI smoke check now runs as a module from `backend/` because
`service.py` imports `inference.labels`:
`python -m inference.service`.

### Geolocation proof of concept

This demo shows the full detection-to-coordinates path before real device
metadata is supported. **Every coordinate it produces is a demo estimate from
assumed inputs.** The seed image `inference/data/uploads/multi.png` is treated
as though it were captured at London Bridge, so its coordinates do not establish
where the photo was actually taken.

**Switch.** `INFRASCAN_POC_GEOLOCATION` is read by the API at startup (`1`,
`true`, `yes`, `on` enable it; `0`, `false`, `no`, `off`, or unset disable it;
anything else fails startup). `compose.yaml` enables it for the `api` service by
default; run `INFRASCAN_POC_GEOLOCATION=0 docker compose up` to turn it off.
While it is enabled, the API logs a warning, and every accepted submission:

- still goes through the normal validation and returns `202` with a job ID;
- is stored with the London Bridge source GPS as the job's
  `latitude`/`longitude`, regardless of the request metadata or any EXIF GPS
  (the request values are kept in `geolocation.request_gps` with
  `used: false`);
- stores the full camera setup as `geolocation` provenance. The worker
  geolocates from that persisted provenance, not from its own environment, so
  results do not depend on how the worker was configured.

The real detector runs on every submission. Jobs submitted with the switch off
keep the request GPS and get no detection locations.

**Configuration** (`backend/app/geolocation/poc.py`, `LONDON_BRIDGE_POC`):

| Setting | Value |
| --- | --- |
| Source | London Bridge, London, UK, 51.507921, -0.087700 (WGS84) |
| Camera | Samsung Galaxy Z Flip7 rear main camera at 1×, 1.5 m above a flat road |
| Orientation (assumed, not measured) | heading 0° (clockwise from true north), pitch 20° down, roll 0° |
| Contact point | bottom center of each bounding box |
| Stable range | ground distance ≤ 50 m and ray ≥ 1° below horizontal |

No measured Flip7 calibration exists in this repository, so the profile is
`GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED` (`kind: diagonal_fov_estimate`,
`calibrated: false`). It treats the published 85° field of view as diagonal
and assumes square pixels, a centered principal point, zero distortion, and an
uncropped image. For an image of `w × h` oriented pixels:

\[ f = \frac{\sqrt{w^2 + h^2}}{2\tan(42.5^\circ)},\quad f_x = f_y = f,\quad c_x = w/2,\quad c_y = h/2 \]

The intrinsics are evaluated at the oriented size of each analyzed image (the
same grid as its bounding boxes; the model's 640 letterbox grid is never used),
so resized uploads give the same ground positions. To use a real calibration,
build a `CalibratedProfile` with a `PinholeIntrinsics` (fx, fy, cx, cy, the
calibration width and height, and OpenCV `(k1, k2, p1, p2[, k3])`
coefficients) and pass it as the `profile` of a `CameraSetup`. It is scaled
uniformly to each image, and images with a different aspect ratio (crops or
rotations) get `intrinsics_mismatch` rather than a guessed location.

**Method** (`backend/app/geolocation/`): undistort the contact pixel if the
profile has real coefficients, back-project it through the intrinsic matrix,
rotate the ray into a local east-north-up frame using the orientation,
intersect it with the road plane 1.5 m below the camera, and convert the
east/north offset to WGS84 using the ellipsoid's meridional and prime-vertical
radii. The module docstring in `projection.py` defines the axes, units, and
signs. A projection that cannot be trusted keeps the detection, sets
`location` to NULL, and records why in `location_status`; it never produces a
fabricated coordinate or fails the job:

| `location_status` | Meaning |
| --- | --- |
| `estimated` | `location` is set |
| `above_horizon` | the ray points at or above the horizon |
| `behind_camera` | the plane intersection lies behind the camera |
| `unstable_near_horizon` | the intersection is beyond 50 m or less than 1° below horizontal, where small pixel errors cause large distance errors |
| `outside_image` | the contact point is outside the image |
| `nonfinite` | an input or intermediate value is not finite |
| `undistortion_failed` | undistortion did not converge |
| `intrinsics_mismatch` | calibrated intrinsics cannot map onto this image's dimensions |

**Seed command.** From `backend/`, with `DATABASE_URL` set and the schema at
head:

```bash
INFRASCAN_POC_GEOLOCATION=1 python -m app.seed_poc               # runs the model in this process
INFRASCAN_POC_GEOLOCATION=1 python -m app.seed_poc --via-worker  # publishes to Celery and waits
```

With Docker Compose, use the worker image (it has the model dependencies):
`docker compose run --rm -e INFRASCAN_POC_GEOLOCATION=1 worker python -m app.seed_poc`.

The command converts a temporary copy of `multi.png` to JPEG (quality 95; the
original file is not modified), submits it through the same service path as
`POST /api/v1/inference/jobs`, runs the real v0-mvp model, and prints the job
ID, each detection with its demo coordinates, and the job, detections,
annotated image, and frontend URLs. It is repeatable: it takes a PostgreSQL
advisory lock and reuses an existing completed (or still running/pending) seed
job identified by its reserved filename and exact JPEG bytes, so later runs
create no new records. Only a history of failed seed jobs triggers a new
submission. The detection count comes from the submitted JPEG bytes and can
differ slightly from the PNG test fixture (four boxes instead of five, because
one borderline box falls below the confidence threshold after re-encoding).
Options: `--source PATH`, `--timeout SECONDS`, and `--api-base-url` /
`--frontend-url` (or `INFRASCAN_PUBLIC_API_URL` / `INFRASCAN_FRONTEND_URL`) for
the printed links.

**Running the demo:**

1. Start PostgreSQL and RabbitMQ, apply migrations, and start the API (with
   `INFRASCAN_POC_GEOLOCATION=1`) and the worker, as in
   [Running the inference workflow](#running-the-inference-workflow).
2. Run the seed command above and note the printed job ID.
3. Start the frontend: `cd "frontend/InfraScan frontend" && npm install && npm run dev`.
   It calls `VITE_API_BASE_URL` (default `http://127.0.0.1:8000`), and the API
   allows CORS from `http://localhost:5173`.
4. Open `http://localhost:5173/?job=<job_id>` (the seed prints this link). The
   page polls the job until it finishes, then shows the annotated image and,
   for each detection, the classification, confidence, severity, and the GPS
   labeled as a demo estimate. You can also upload a JPEG from the page; the
   GPS fields are sent but ignored while the demo mode is on.

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

`tests/test_geolocation.py` checks the projection math without a database:
known ground points round-trip through pixel space, heading changes rotate the
results, image scaling leaves them unchanged, and rays at or above the horizon
get no coordinate. `tests/test_poc_geolocation_workflow.py` covers the POC
override of request GPS, persisted and returned locations, per-job media
(including zero-detection jobs), the evidence endpoint, and seed idempotency.

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
- pick top 2-3 most popular dashcams in United States and India (1 second will have some number of frames with geo-tagged lat/long & gps trajectory)

- after this, we either need to expand to support more devices with different formats and/or allow users to geo-tag issues after detections

## Current state of Geolocation method
- method: inverse projection mapping
- constraints: each frame will have have corresponding location
- high level:
	- detection_location = camera_or_source_gps + offset_detection_from_camera/source(thru inverse projection mapping)

- current state:
	- prototype have worked
    - wired into the inference pipeline as a demo with an assumed source and camera
      (see [Geolocation proof of concept](#geolocation-proof-of-concept))
    - need extensive testing though
    - Need to find top 2-3 most popular dashcams from India and America and provide support
       - for now, just pick one from each side and provide meta data parsing and extraction 
