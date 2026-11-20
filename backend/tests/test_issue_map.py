"""Issue map, Issue list/detail, and Stanton-to-Anaheim seed tests (PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.models import (
    ISSUE_TYPE_DISPLAY_NAMES,
    MODEL_CLASS_ISSUE_TYPES,
    DetectionRecord,
    EvidenceMedia,
    InferenceJob,
    InputMedia,
    Issue,
)
from app.seeds import stanton_anaheim
from app.services.inference_jobs import DatabaseInfraScanService

SEEN_AT = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)
WORLD = {
    "min_latitude": -90,
    "min_longitude": -180,
    "max_latitude": 90,
    "max_longitude": 180,
}


class NoopQueue:
    def enqueue(self, job_id: UUID) -> None:
        raise AssertionError("Issue reads must not enqueue work")


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> TestClient:
    app = FastAPI()
    app.state.infrascan_service = DatabaseInfraScanService(session_factory, NoopQueue())
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return TestClient(app)


def add_issue(
    session_factory: sessionmaker[Session],
    *,
    latitude: float | None = 33.80,
    longitude: float | None = -117.95,
    issue_type: str = "pothole",
    severity: str = "moderate",
    status: str = "accepted",
    review_reason: str | None = None,
    predicted_classes: tuple[int, ...] = (3,),
    last_seen_at: datetime = SEEN_AT,
    confidences: tuple[float, ...] | None = None,
) -> UUID:
    """Persist one Issue with one detection per predicted class."""
    with session_factory() as session, session.begin():
        media = InputMedia(data=b"\xff\xd8input\xff\xd9", content_type="image/jpeg")
        issue = Issue(
            issue_type_code=issue_type,
            severity=severity,
            status=status,
            review_reason=review_reason,
            latitude=latitude,
            longitude=longitude,
            location_accuracy_m=5.0 if latitude is not None else None,
            first_seen_at=last_seen_at - timedelta(days=7),
            last_seen_at=last_seen_at,
        )
        session.add(issue)
        for index, class_id in enumerate(predicted_classes):
            job = InferenceJob(
                input_media=media,
                status="completed",
                captured_at=last_seen_at,
                latitude=Decimal("33.8"),
                longitude=Decimal("-117.9"),
            )
            evidence = EvidenceMedia(
                job=job,
                data=f"evidence-{index}".encode(),
                content_type="image/jpeg",
            )
            location = (
                {"latitude": latitude, "longitude": longitude}
                if latitude is not None
                else None
            )
            session.add(
                DetectionRecord(
                    job=job,
                    evidence=evidence,
                    issue=issue,
                    issue_type=class_id,
                    confidence=confidences[index] if confidences else 0.8,
                    severity=severity,
                    bounding_box=[1.0, 2.0, 30.0, 40.0],
                    location=location,
                    detected_at=last_seen_at - timedelta(days=index),
                )
            )
        session.flush()
        return issue.issue_id


def get_map(client: TestClient, **params) -> dict:
    response = client.get("/api/v1/issues/map", params={**WORLD, **params})
    assert response.status_code == 200, response.text
    return response.json()


def feature_ids(body: dict) -> set[str]:
    return {feature["id"] for feature in body["features"]}


def test_map_returns_one_feature_per_issue_with_detection_counts(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    repeated = add_issue(session_factory, predicted_classes=(3, 3, 3, 3, 3))
    single = add_issue(session_factory, latitude=33.81, predicted_classes=(0,),
                       issue_type="longitudinal_crack")

    body = get_map(client)

    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 2
    counts = {f["id"]: f["properties"]["detection_count"] for f in body["features"]}
    assert counts == {str(repeated): 5, str(single): 1}
    assert body["meta"] == {"limit": 500, "returned": 2, "total": 2, "truncated": False}


def test_map_geometry_is_longitude_then_latitude(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    issue_id = add_issue(session_factory, latitude=33.8025, longitude=-117.9932)

    body = get_map(
        client, min_latitude=33.7, min_longitude=-118.1, max_latitude=33.9,
        max_longitude=-117.8,
    )

    (feature,) = body["features"]
    assert feature["id"] == str(issue_id)
    assert feature["geometry"] == {"type": "Point", "coordinates": [-117.9932, 33.8025]}
    assert feature["properties"]["issue_type_code"] == "pothole"
    assert feature["properties"]["issue_type_name"] == "Pothole"
    assert "latitude" not in feature["properties"]
    assert body["bbox"] == [-118.1, 33.7, -117.8, 33.9]


def test_bounding_box_does_not_match_with_axes_swapped(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    issue_id = add_issue(session_factory, latitude=10.0, longitude=20.0)

    correct = get_map(client, min_latitude=5, max_latitude=15, min_longitude=15, max_longitude=25)
    swapped = get_map(client, min_latitude=15, max_latitude=25, min_longitude=5, max_longitude=15)

    assert feature_ids(correct) == {str(issue_id)}
    assert swapped["features"] == []


def test_bounding_box_filtering_is_inclusive(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    inside = add_issue(session_factory, latitude=33.80, longitude=-117.95)
    on_corner = add_issue(session_factory, latitude=33.90, longitude=-117.90)
    north = add_issue(session_factory, latitude=33.9001, longitude=-117.95)
    west = add_issue(session_factory, latitude=33.80, longitude=-118.0001)

    body = get_map(
        client, min_latitude=33.70, min_longitude=-118.0, max_latitude=33.90,
        max_longitude=-117.90,
    )

    assert feature_ids(body) == {str(inside), str(on_corner)}
    assert str(north) not in feature_ids(body)
    assert str(west) not in feature_ids(body)


def test_unlocated_issue_is_excluded_from_map_but_kept_in_review_queue(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    located = add_issue(session_factory)
    unlocated = add_issue(
        session_factory, latitude=None, longitude=None, status="pending_review",
        review_reason="missing_location",
    )

    assert feature_ids(get_map(client)) == {str(located)}
    assert get_map(client, review_required=True)["features"] == []

    queue = client.get("/api/v1/issues/review-queue").json()
    assert [item["issue_id"] for item in queue["items"]] == [str(unlocated)]
    assert queue["items"][0]["latitude"] is None
    assert queue["items"][0]["review_reason"] == "missing_location"

    unfiltered = client.get("/api/v1/issues").json()
    assert {item["issue_id"] for item in unfiltered["items"]} == {str(located), str(unlocated)}
    in_box = client.get("/api/v1/issues", params=WORLD).json()
    assert [item["issue_id"] for item in in_box["items"]] == [str(located)]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"issue_type": "pothole"}, {"pothole", "conflict"}),
        ({"issue_type": "alligator_crack"}, {"alligator"}),
        (
            {"issue_type": ["longitudinal_crack", "alligator_crack"]},
            {"alligator", "crack"},
        ),
        ({"severity": "critical"}, {"alligator"}),
        ({"severity": ["minor", "critical"]}, {"alligator", "crack"}),
        ({"status": "resolved"}, {"crack"}),
        ({"review_required": True}, {"conflict"}),
        ({"last_seen_after": "2026-10-20T00:00:00Z"}, {"pothole", "alligator", "conflict"}),
        ({"last_seen_before": "2026-10-20T00:00:00Z"}, {"crack"}),
        ({"issue_type": "pothole", "status": "pending_review"}, {"conflict"}),
    ],
)
def test_map_filters_are_applied_by_the_server(
    session_factory: sessionmaker[Session],
    client: TestClient,
    params: dict,
    expected: set[str],
) -> None:
    ids = {
        "pothole": add_issue(session_factory, issue_type="pothole", severity="moderate"),
        "alligator": add_issue(
            session_factory, issue_type="alligator_crack", severity="critical",
            predicted_classes=(2,),
        ),
        "crack": add_issue(
            session_factory, issue_type="longitudinal_crack", severity="minor",
            status="resolved", predicted_classes=(0,),
            last_seen_at=datetime(2026, 10, 1, tzinfo=UTC),
        ),
        "conflict": add_issue(
            session_factory, issue_type="pothole", status="pending_review",
            review_reason="type_conflict", predicted_classes=(3, 2),
        ),
    }

    body = get_map(client, **params)

    assert feature_ids(body) == {str(ids[name]) for name in expected}
    assert body["meta"]["total"] == len(expected)


def test_map_limit_reports_truncation_and_keeps_most_severe(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    critical = add_issue(session_factory, severity="critical")
    moderate = add_issue(session_factory, severity="moderate")
    add_issue(session_factory, severity="minor")

    body = get_map(client, limit=2)

    assert [f["id"] for f in body["features"]] == [str(critical), str(moderate)]
    assert body["meta"] == {"limit": 2, "returned": 2, "total": 3, "truncated": True}


@pytest.mark.parametrize(
    "params",
    [
        {**WORLD, "min_latitude": 10, "max_latitude": 5},
        {**WORLD, "min_longitude": 170, "max_longitude": -170},
        {**WORLD, "max_latitude": 90.5},
        {**WORLD, "min_longitude": -181},
        {**WORLD, "min_latitude": "nan"},
        {k: v for k, v in WORLD.items() if k != "max_longitude"},
        {**WORLD, "severity": "severe"},
        {**WORLD, "issue_type": "sinkhole"},
        {**WORLD, "status": "open"},
        {**WORLD, "limit": 0},
        {**WORLD, "limit": 1001},
        {**WORLD, "last_seen_after": "2026-11-02T00:00:00Z",
         "last_seen_before": "2026-11-01T00:00:00Z"},
        {**WORLD, "last_seen_after": "2026-11-02T00:00:00"},
    ],
)
def test_map_rejects_invalid_query_parameters(client: TestClient, params: dict) -> None:
    assert client.get("/api/v1/issues/map", params=params).status_code == 422


def test_issue_list_requires_complete_bounding_box(client: TestClient) -> None:
    response = client.get("/api/v1/issues", params={"min_latitude": 1})
    assert response.status_code == 422


def test_issue_detail_and_detections_preserve_raw_predicted_classes(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    issue_id = add_issue(
        session_factory, status="pending_review", review_reason="type_conflict",
        predicted_classes=(3, 2, 3), confidences=(0.7, 0.9, 0.6),
    )

    detail = client.get(f"/api/v1/issues/{issue_id}").json()
    assert detail["issue_type_code"] == "pothole"
    assert detail["detection_count"] == 3
    assert detail["needs_review"] is True
    assert detail["review_reason"] == "type_conflict"
    assert (detail["latitude"], detail["longitude"]) == (33.80, -117.95)

    detections = client.get(f"/api/v1/issues/{issue_id}/detections").json()
    assert detections["total"] == 3
    predicted = sorted(
        (d["issue_type"], d["classification"], d["predicted_issue_type_code"])
        for d in detections["items"]
    )
    assert predicted == [
        (2, "Alligator crack", "alligator_crack"),
        (3, "Pothole", "pothole"),
        (3, "Pothole", "pothole"),
    ]
    assert {d["issue_id"] for d in detections["items"]} == {str(issue_id)}

    # The representative thumbnail is the most confident detection's evidence.
    best = max(detections["items"], key=lambda d: d["confidence"])
    assert detail["evidence_thumbnail_url"] == f"/api/v1/evidence/{best['evidence_media']}"
    image = client.get(detail["evidence_thumbnail_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.content == b"evidence-1"


def test_unknown_issue_and_evidence_return_404(client: TestClient) -> None:
    missing = uuid4()
    for path in (
        f"/api/v1/issues/{missing}",
        f"/api/v1/issues/{missing}/detections",
        f"/api/v1/evidence/{missing}",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] in {"ISSUE_NOT_FOUND", "EVIDENCE_NOT_FOUND"}


def test_issue_types_reference_rows_match_model_class_mapping(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        rows = session.execute(
            sa.text("SELECT code, display_name, model_class_id FROM issue_types")
        ).all()
    assert {(code, name, class_id) for code, name, class_id in rows} == {
        (code.value, ISSUE_TYPE_DISPLAY_NAMES[code], class_id)
        for class_id, code in MODEL_CLASS_ISSUE_TYPES.items()
    }


def test_issue_coordinates_must_be_paired(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError):
        add_issue(session_factory, latitude=33.8, longitude=None)


def test_viewport_query_can_use_location_index(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.execute(sa.text("SET LOCAL enable_seqscan = off"))
        plan = session.execute(
            sa.text(
                "EXPLAIN SELECT issue_id FROM issues "
                "WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
                "AND point(longitude, latitude) <@ box(point(-118, 33.7), point(-117.9, 33.9))"
            )
        ).scalars().all()
    assert any("ix_issues_location" in line for line in plan), plan


def test_issue_filter_indexes_exist(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        indexes = set(
            session.execute(
                sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'issues'")
            ).scalars()
        )
    assert {
        "ix_issues_issue_type_code",
        "ix_issues_status",
        "ix_issues_last_seen_at",
        "ix_issues_location",
    } <= indexes


def _run_seed(session_factory: sessionmaker[Session]) -> stanton_anaheim.SeedSummary:
    with session_factory() as session, session.begin():
        return stanton_anaheim.seed(session)


def test_stanton_anaheim_seed_produces_expected_map(
    session_factory: sessionmaker[Session], client: TestClient
) -> None:
    _run_seed(session_factory)
    summary = _run_seed(session_factory)  # Idempotent: replaces the first run.

    assert summary == stanton_anaheim.SeedSummary(
        issues=10, located_issues=8, unlocated_issues=2, detections=21
    )
    min_lat, min_lon, max_lat, max_lon = stanton_anaheim.route_bounds()
    route_box = {
        "min_latitude": min_lat, "min_longitude": min_lon,
        "max_latitude": max_lat, "max_longitude": max_lon,
    }
    body = get_map(client, **route_box)

    # Every located Issue is framed by the route bounds; one feature per Issue.
    assert body["meta"]["total"] == summary.located_issues
    assert len(body["features"]) == summary.located_issues
    assert get_map(client)["meta"]["total"] == summary.located_issues
    by_id = {UUID(f["id"]): f for f in body["features"]}

    repeated = by_id[stanton_anaheim.seed_id("issue", "katella-pothole-repeated")]
    assert repeated["properties"]["detection_count"] == 3

    conflict = by_id[stanton_anaheim.seed_id("issue", "katella-type-conflict")]
    assert conflict["properties"]["review_reason"] == "type_conflict"
    assert conflict["properties"]["needs_review"] is True
    assert conflict["properties"]["detection_count"] == 3
    predicted = {
        d["predicted_issue_type_code"]
        for d in client.get(f"/api/v1/issues/{conflict['id']}/detections").json()["items"]
    }
    assert predicted == {"pothole", "alligator_crack"}

    unlocated_ids = {
        str(stanton_anaheim.seed_id("issue", key))
        for key in ("unlocated-pothole", "unlocated-transverse")
    }
    assert not unlocated_ids & {f["id"] for f in body["features"]}
    queue = client.get("/api/v1/issues/review-queue").json()
    assert unlocated_ids <= {item["issue_id"] for item in queue["items"]}

    with session_factory() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Issue)) == 10
        assert session.scalar(sa.select(sa.func.count()).select_from(DetectionRecord)) == 21
        assert session.scalar(
            sa.select(sa.func.count()).select_from(DetectionRecord)
            .where(DetectionRecord.issue_id.is_(None))
        ) == 0
