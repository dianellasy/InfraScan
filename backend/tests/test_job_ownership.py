"""Job, detection, and evidence ownership through the HTTP API (PostgreSQL-backed)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.models import DetectionRecord, EvidenceMedia, InferenceJob, InputMedia, Issue
from app.services.inference_jobs import DatabaseInfraScanService
from auth_support import (
    CITIZEN_A,
    CITIZEN_B,
    CREW_MEMBER,
    administrator,
    auth_headers,
    citizen_a,
    citizen_b,
    developer,
)
from test_inference_workflow import (
    VALID_METADATA,
    InMemoryMediaStorage,
    RecordingQueue,
    make_jpeg,
)

SEEN_AT = datetime(2026, 11, 10, 22, 30, tzinfo=UTC)
NOT_FOUND = {
    "job": {"code": "JOB_NOT_FOUND", "message": "The inference job was not found.", "field": "job_id"},
    "evidence": {
        "code": "EVIDENCE_NOT_FOUND",
        "message": "The evidence image was not found.",
        "field": "media_id",
    },
}


@dataclass(frozen=True)
class SeededJob:
    job_id: UUID
    detection_id: UUID
    evidence_id: UUID
    evidence_bytes: bytes


@pytest.fixture
def media_storage() -> InMemoryMediaStorage:
    return InMemoryMediaStorage()


@pytest.fixture
def app(session_factory: sessionmaker[Session], media_storage: InMemoryMediaStorage) -> FastAPI:
    app = FastAPI()
    app.state.infrascan_service = DatabaseInfraScanService(
        session_factory, RecordingQueue(session_factory), media_storage
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def shared_issue(session_factory: sessionmaker[Session]) -> UUID:
    with session_factory() as session, session.begin():
        issue = Issue(
            issue_type_code="pothole",
            severity="moderate",
            status="pending_review",
            latitude=33.8,
            longitude=-117.9,
            location_accuracy_m=5.0,
            first_seen_at=SEEN_AT,
            last_seen_at=SEEN_AT,
        )
        session.add(issue)
        session.flush()
        return issue.issue_id


def seed_job(
    session_factory: sessionmaker[Session],
    owner: UUID | None,
    issue_id: UUID,
    confidence: float = 0.8,
) -> SeededJob:
    """A completed job owned by `owner` (None: historical) with one detection on `issue_id`."""
    evidence_bytes = f"evidence-{owner}-{uuid4()}".encode()
    with session_factory() as session, session.begin():
        job = InferenceJob(
            user_id=owner,
            input_media=InputMedia(data=b"\xff\xd8input\xff\xd9", content_type="image/jpeg"),
            status="completed",
            model_version="v0-mvp",
            captured_at=SEEN_AT,
            latitude=Decimal("33.8"),
            longitude=Decimal("-117.9"),
        )
        evidence = EvidenceMedia(job=job, data=evidence_bytes, content_type="image/jpeg")
        detection = DetectionRecord(
            job=job,
            evidence=evidence,
            issue_id=issue_id,
            issue_type=3,
            confidence=confidence,
            severity="moderate",
            bounding_box=[1.0, 2.0, 30.0, 40.0],
            location={"latitude": 33.8, "longitude": -117.9},
            detected_at=SEEN_AT,
            observed_at=SEEN_AT,
        )
        session.add(detection)
        session.flush()
        return SeededJob(job.job_id, detection.detection_id, evidence.media_id, evidence_bytes)


def read_job(client: TestClient, job: SeededJob, headers: dict[str, str]) -> list:
    return [
        client.get(f"/api/v1/jobs/{job.job_id}", headers=headers),
        client.get(f"/api/v1/jobs/{job.job_id}/detections", headers=headers),
        client.get(f"/api/v1/evidence/{job.evidence_id}", headers=headers),
    ]


def assert_can_read(client: TestClient, job: SeededJob, headers: dict[str, str]) -> None:
    detail, detections, evidence = read_job(client, job, headers)
    assert detail.status_code == 200
    assert detail.json()["job_id"] == str(job.job_id)
    assert detections.status_code == 200
    assert [d["detection_id"] for d in detections.json()["items"]] == [str(job.detection_id)]
    assert evidence.status_code == 200
    assert evidence.content == job.evidence_bytes
    assert evidence.headers["cache-control"].startswith("private")
    assert evidence.headers["vary"] == "Authorization"


def assert_hidden(client: TestClient, job: SeededJob, headers: dict[str, str]) -> None:
    """Indistinguishable from an ID that does not exist: 404, never 403."""
    detail, detections, evidence = read_job(client, job, headers)
    assert (detail.status_code, detail.json()) == (404, {"error": NOT_FOUND["job"]})
    assert (detections.status_code, detections.json()) == (404, {"error": NOT_FOUND["job"]})
    assert (evidence.status_code, evidence.json()) == (404, {"error": NOT_FOUND["evidence"]})
    unknown = client.get(f"/api/v1/jobs/{uuid4()}", headers=headers)
    assert unknown.json() == detail.json()


# --- Submission ownership ---


def submit(client: TestClient, headers: dict[str, str], metadata: dict | None = None, **kw):
    return client.post(
        "/api/v1/inference/jobs",
        headers=headers,
        files=[
            ("file", ("road.jpg", make_jpeg(), "image/jpeg")),
            ("metadata", (None, json.dumps(metadata or VALID_METADATA))),
            *kw.get("extra_parts", []),
        ],
        params=kw.get("params"),
    )


def job_owners(session_factory: sessionmaker[Session]) -> list[UUID | None]:
    with session_factory() as session:
        return list(session.scalars(sa.select(InferenceJob.user_id)))


def test_authenticated_submission_stores_the_token_subject(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    response = submit(client, citizen_a(), params={"user_id": str(CITIZEN_B)})

    assert response.status_code == 202
    job_id = UUID(response.json()["job_id"])
    with session_factory() as session:
        assert session.get(InferenceJob, job_id).user_id == CITIZEN_A
    assert client.get(f"/api/v1/jobs/{job_id}", headers=citizen_a()).status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}", headers=citizen_b()).status_code == 404


@pytest.mark.parametrize(
    "role", ["citizen", "maintenance_crew", "city_engineer", "municipal_administrator", "developer"]
)
def test_every_role_may_submit_and_owns_the_job(
    client: TestClient, session_factory: sessionmaker[Session], role: str
) -> None:
    response = submit(client, auth_headers(CREW_MEMBER, role=role))

    assert response.status_code == 202
    assert job_owners(session_factory) == [CREW_MEMBER]


def test_client_supplied_user_id_is_rejected(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    in_metadata = submit(client, citizen_a(), {**VALID_METADATA, "user_id": str(CITIZEN_B)})
    as_form_field = submit(
        client, citizen_a(), extra_parts=[("user_id", (None, str(CITIZEN_B)))]
    )

    assert in_metadata.status_code == 422
    assert in_metadata.json()["error"] == {
        "code": "UNKNOWN_METADATA_FIELD",
        "message": "Metadata contains an unknown field.",
        "field": "metadata.user_id",
    }
    assert as_form_field.status_code == 400
    assert as_form_field.json()["error"]["code"] == "UNEXPECTED_MULTIPART_FIELD"
    assert job_owners(session_factory) == []


def test_unauthenticated_submission_creates_nothing(
    client: TestClient, session_factory: sessionmaker[Session], media_storage
) -> None:
    response = submit(client, {})

    assert response.status_code == 401
    assert job_owners(session_factory) == []
    assert media_storage.puts == []


# --- Reading jobs, detections, and evidence ---


def test_owner_reads_own_job_detections_and_evidence(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID
) -> None:
    job = seed_job(session_factory, CITIZEN_A, shared_issue)
    assert_can_read(client, job, citizen_a())


def test_other_user_gets_404_for_job_detections_and_evidence(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID
) -> None:
    job = seed_job(session_factory, CITIZEN_A, shared_issue)
    assert_hidden(client, job, citizen_b())


@pytest.mark.parametrize("role", ["maintenance_crew", "city_engineer"])
def test_crew_and_engineers_stay_owner_scoped(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID, role: str
) -> None:
    job = seed_job(session_factory, CITIZEN_A, shared_issue)
    own = seed_job(session_factory, CREW_MEMBER, shared_issue)
    headers = auth_headers(CREW_MEMBER, role=role, municipality_id="stanton")

    assert_hidden(client, job, headers)
    assert_can_read(client, own, headers)


def test_user_metadata_role_does_not_grant_access(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID
) -> None:
    job = seed_job(session_factory, CITIZEN_A, shared_issue)
    headers = auth_headers(CITIZEN_B, user_metadata={"role": "developer"})
    assert_hidden(client, job, headers)


@pytest.mark.parametrize("headers", [administrator, developer])
def test_administrator_and_developer_read_any_users_job(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID, headers
) -> None:
    job = seed_job(session_factory, CITIZEN_A, shared_issue)
    assert_can_read(client, job, headers())


@pytest.mark.parametrize(
    "headers",
    [citizen_a, lambda: auth_headers(CREW_MEMBER, role="maintenance_crew"),
     lambda: auth_headers(CREW_MEMBER, role="city_engineer")],
)
def test_ordinary_users_cannot_read_historical_ownerless_jobs(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID, headers
) -> None:
    historical = seed_job(session_factory, None, shared_issue)
    assert_hidden(client, historical, headers())


@pytest.mark.parametrize("headers", [administrator, developer])
def test_administrator_and_developer_read_historical_ownerless_jobs(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID, headers
) -> None:
    historical = seed_job(session_factory, None, shared_issue)
    assert_can_read(client, historical, headers())


# --- Shared Issues expose only the caller's detections and evidence ---


def test_shared_issue_scopes_detections_and_thumbnail_per_caller(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID
) -> None:
    job_a = seed_job(session_factory, CITIZEN_A, shared_issue, confidence=0.6)
    job_b = seed_job(session_factory, CITIZEN_B, shared_issue, confidence=0.9)
    historical = seed_job(session_factory, None, shared_issue, confidence=0.7)
    stranger = auth_headers(uuid4())

    def view(headers):
        issue = client.get(f"/api/v1/issues/{shared_issue}", headers=headers).json()
        detections = client.get(
            f"/api/v1/issues/{shared_issue}/detections", headers=headers
        ).json()
        return issue, {d["detection_id"] for d in detections["items"]}, detections["total"]

    issue_a, seen_a, total_a = view(citizen_a())
    issue_b, seen_b, total_b = view(citizen_b())
    issue_x, seen_x, total_x = view(stranger)
    issue_admin, seen_admin, total_admin = view(administrator())

    # The Issue itself, including its total detection count, is shared.
    assert issue_a["detection_count"] == issue_x["detection_count"] == 3
    assert (seen_a, total_a) == ({str(job_a.detection_id)}, 1)
    assert (seen_b, total_b) == ({str(job_b.detection_id)}, 1)
    assert (seen_x, total_x) == (set(), 0)
    assert total_admin == 3 and seen_admin == {
        str(job_a.detection_id), str(job_b.detection_id), str(historical.detection_id)
    }
    # Thumbnails come only from evidence the caller can open.
    assert issue_a["evidence_thumbnail_url"] == f"/api/v1/evidence/{job_a.evidence_id}"
    assert issue_b["evidence_thumbnail_url"] == f"/api/v1/evidence/{job_b.evidence_id}"
    assert issue_x["evidence_thumbnail_url"] is None
    assert issue_admin["evidence_thumbnail_url"] == f"/api/v1/evidence/{job_b.evidence_id}"
    assert client.get(issue_a["evidence_thumbnail_url"], headers=citizen_a()).status_code == 200

    world = {"min_latitude": -90, "max_latitude": 90, "min_longitude": -180, "max_longitude": 180}
    feature_a = client.get("/api/v1/issues/map", params=world, headers=citizen_a()).json()
    feature_x = client.get("/api/v1/issues/map", params=world, headers=stranger).json()
    assert feature_a["features"][0]["properties"]["evidence_thumbnail_url"] == (
        f"/api/v1/evidence/{job_a.evidence_id}"
    )
    assert feature_x["features"][0]["properties"]["evidence_thumbnail_url"] is None
    queue = client.get("/api/v1/issues/review-queue", headers=stranger).json()
    assert queue["items"][0]["evidence_thumbnail_url"] is None


def test_job_issue_filter_matches_only_accessible_jobs(
    client: TestClient, session_factory: sessionmaker[Session], shared_issue: UUID
) -> None:
    job_a = seed_job(session_factory, CITIZEN_A, shared_issue)

    def job_issues(headers):
        return client.get(
            "/api/v1/issues", params={"job_id": str(job_a.job_id)}, headers=headers
        ).json()["total"]

    assert job_issues(citizen_a()) == 1
    assert job_issues(citizen_b()) == 0
    assert job_issues(developer()) == 1
