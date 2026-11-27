"""Issue review decisions (PATCH /issues/{id}) and the detection register (PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.models import DetectionRecord, EvidenceMedia, InferenceJob, InputMedia, Issue
from app.services.inference_jobs import DatabaseInfraScanService
from auth_support import ADMIN, DEVELOPER, auth_headers, citizen_a, developer

SEEN_AT = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)


class NoopQueue:
    def enqueue(self, job_id: UUID) -> None:
        raise AssertionError("must not enqueue")


@pytest.fixture
def app(session_factory: sessionmaker[Session]) -> FastAPI:
    test_app = FastAPI()
    test_app.state.infrascan_service = DatabaseInfraScanService(session_factory, NoopQueue())
    register_exception_handlers(test_app)
    test_app.include_router(api_router, prefix="/api/v1")
    return test_app


def seed(
    session_factory: sessionmaker[Session],
    *,
    status: str = "pending_review",
    review_reason: str | None = "low_confidence",
    detections: tuple[tuple[int, float, str], ...] = ((3, 0.81, "moderate"),),
    detected_at: datetime = SEEN_AT,
    latitude: float = 33.80,
) -> tuple[UUID, list[UUID]]:
    """One Issue with detections given as (class id, confidence, severity)."""
    with session_factory() as session, session.begin():
        issue = Issue(
            issue_type_code="pothole",
            severity="moderate",
            status=status,
            review_reason=review_reason,
            latitude=latitude,
            longitude=-117.95,
            location_accuracy_m=5.0,
            first_seen_at=detected_at - timedelta(days=1),
            last_seen_at=detected_at,
        )
        job = InferenceJob(
            input_media=InputMedia(data=b"\xff\xd8in\xff\xd9", content_type="image/jpeg"),
            status="completed",
            captured_at=detected_at,
            latitude=Decimal("33.8"),
            longitude=Decimal("-117.9"),
        )
        evidence = EvidenceMedia(job=job, data=b"evidence", content_type="image/jpeg")
        records = [
            DetectionRecord(
                job=job,
                evidence=evidence,
                issue=issue,
                issue_type=class_id,
                confidence=confidence,
                severity=severity,
                bounding_box=[1.0, 2.0, 30.0, 40.0],
                location={"latitude": latitude, "longitude": -117.95},
                detected_at=detected_at - timedelta(minutes=index),
            )
            for index, (class_id, confidence, severity) in enumerate(detections)
        ]
        session.add_all([issue, *records])
        session.flush()
        return issue.issue_id, [record.detection_id for record in records]


# --- Review decisions ---


def test_accepting_an_issue_records_the_reviewer_and_clears_the_flag(app, session_factory) -> None:
    issue_id, _ = seed(session_factory)
    with TestClient(app, headers=developer()) as client:
        response = client.patch(
            f"/api/v1/issues/{issue_id}",
            json={"status": "accepted", "review_note": "  Clear pothole, 30 cm wide.  "},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["review_reason"] is None
    assert body["needs_review"] is False
    assert body["review_note"] == "Clear pothole, 30 cm wide."
    assert body["reviewed_at"] is not None
    with session_factory() as session:
        issue = session.get(Issue, issue_id)
        assert issue.reviewed_by == DEVELOPER


def test_dismissed_issue_leaves_the_review_queue(app, session_factory) -> None:
    issue_id, _ = seed(session_factory)
    with TestClient(app, headers=developer()) as client:
        assert client.patch(f"/api/v1/issues/{issue_id}", json={"status": "dismissed"}).status_code == 200
        queue = client.get("/api/v1/issues/review-queue").json()

    assert issue_id not in {UUID(item["issue_id"]) for item in queue["items"]}


def test_severity_can_be_corrected_without_changing_status(app, session_factory) -> None:
    issue_id, _ = seed(session_factory, status="accepted", review_reason=None)
    with TestClient(app, headers=developer()) as client:
        body = client.patch(f"/api/v1/issues/{issue_id}", json={"severity": "critical"}).json()

    assert (body["severity"], body["status"]) == ("critical", "accepted")


def test_reopening_keeps_the_issue_pending(app, session_factory) -> None:
    issue_id, _ = seed(session_factory, status="dismissed", review_reason=None)
    with TestClient(app, headers=developer()) as client:
        body = client.patch(f"/api/v1/issues/{issue_id}", json={"status": "pending_review"}).json()

    assert body["status"] == "pending_review"
    assert body["needs_review"] is True


@pytest.mark.parametrize(
    "body",
    [{}, {"status": "fixed"}, {"severity": "huge"}, {"title": "x"}, {"review_note": "x" * 2001}],
)
def test_invalid_review_bodies_are_rejected(app, session_factory, body) -> None:
    issue_id, _ = seed(session_factory)
    with TestClient(app, headers=developer()) as client:
        response = client.patch(f"/api/v1/issues/{issue_id}", json=body)

    assert response.status_code == 422


def test_reviewing_an_unknown_issue_is_404(app) -> None:
    with TestClient(app, headers=developer()) as client:
        response = client.patch(f"/api/v1/issues/{uuid4()}", json={"status": "accepted"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ISSUE_NOT_FOUND"


def test_citizens_cannot_review(app, session_factory) -> None:
    issue_id, _ = seed(session_factory)
    with TestClient(app, headers=citizen_a()) as client:
        response = client.patch(f"/api/v1/issues/{issue_id}", json={"status": "accepted"})

    assert response.status_code == 403


# --- Detection register ---


def test_register_lists_every_detection_with_its_issue_status(app, session_factory) -> None:
    pending, pending_detections = seed(session_factory)
    dismissed, dismissed_detections = seed(
        session_factory, status="dismissed", review_reason=None,
        detected_at=SEEN_AT - timedelta(days=2),
    )
    with TestClient(app, headers=auth_headers(ADMIN, role="municipal_administrator")) as client:
        body = client.get("/api/v1/detections").json()

    assert body["total"] == 2
    assert [item["detection_id"] for item in body["items"]] == [
        str(pending_detections[0]), str(dismissed_detections[0]),
    ]  # newest first
    assert [item["issue_status"] for item in body["items"]] == ["pending_review", "dismissed"]
    assert body["items"][0]["classification"] == "Pothole"
    assert body["items"][0]["evidence_media"] is not None


def test_register_filters_by_label_severity_confidence_and_area(app, session_factory) -> None:
    seed(
        session_factory,
        detections=((3, 0.9, "critical"), (0, 0.4, "minor"), (2, 0.6, "moderate")),
    )
    seed(session_factory, latitude=10.0, detections=((3, 0.95, "critical"),))
    with TestClient(app, headers=developer()) as client:
        def total(**params) -> int:
            response = client.get("/api/v1/detections", params=params)
            assert response.status_code == 200, response.text
            return response.json()["total"]

        assert total() == 4
        assert total(classification="pothole") == 2
        assert total(classification="Not a class") == 0
        assert total(severity="minor") == 1
        assert total(confidence_min=0.5, confidence_max=0.92) == 2
        assert total(min_latitude=30, max_latitude=40, min_longitude=-120, max_longitude=-110) == 3


def test_register_pages(app, session_factory) -> None:
    seed(session_factory, detections=tuple((3, 0.5 + i / 100, "minor") for i in range(5)))
    with TestClient(app, headers=developer()) as client:
        first = client.get("/api/v1/detections", params={"page_size": 2}).json()
        last = client.get("/api/v1/detections", params={"page_size": 2, "page": 3}).json()

    assert (first["total"], len(first["items"]), len(last["items"])) == (5, 2, 1)


def test_one_detection_and_unknown_detection(app, session_factory) -> None:
    _, (detection_id,) = seed(session_factory)
    with TestClient(app, headers=developer()) as client:
        found = client.get(f"/api/v1/detections/{detection_id}")
        missing = client.get(f"/api/v1/detections/{uuid4()}")

    assert found.status_code == 200
    assert found.json()["issue_status"] == "pending_review"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "DETECTION_NOT_FOUND"


def test_register_is_privileged(app) -> None:
    with TestClient(app, headers=citizen_a()) as client:
        assert client.get("/api/v1/detections").status_code == 403
