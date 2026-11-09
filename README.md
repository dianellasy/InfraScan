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
- GoPro - GPMF (1 second will have some number of frames with geo-tagged lat/long & gps trajectory)

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
