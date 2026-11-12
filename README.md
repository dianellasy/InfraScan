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
declared in `backend/app/adapters/ports.py`. Concrete service and adapter
implementations are not configured yet; until an application service is
attached to `app.state.infrascan_service`, API calls return `503 Service
Unavailable` rather than successful placeholder data.

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

The eventual application service must store the media, create and commit the
database job with its metadata and stored-media reference, enqueue work by the
application job ID, and return the acceptance DTO. The database job must be
committed before a worker can consume it, and dispatch failures need explicit
handling. Those storage, database, queue, tracking, and authentication
implementations are not part of the controller milestone.

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
  `TIMESTAMPTZ`), `latitude` (required `NUMERIC` in `[-90, 90]`), and
  `longitude` (required `NUMERIC` in `[-180, 180]`).

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
database health check before starting. The current API does not yet configure a
database adapter, so this provides the container and connection settings but
does not enable persistence. The example database credentials are for local
development only; change them in `.env` before using this setup elsewhere.

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
    - need to wire up with the rest project
    - need extensive testing though
    - Need to find top 2-3 most popular dashcams from India and America and provide support
       - for now, just pick one from each side and provide meta data parsing and extraction 
