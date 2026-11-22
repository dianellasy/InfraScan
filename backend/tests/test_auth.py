"""Supabase access-token verification, roles, CORS, and route protection (no database)."""

from __future__ import annotations

import logging
import urllib.error
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth
from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.authorization import AuthenticatedUser, JobAccess, user_from_claims
from app.services.inference_jobs import DatabaseInfraScanService
from auth_support import (
    ADMIN,
    CITIZEN_A,
    CREW_MEMBER,
    DEVELOPER,
    OTHER_KEY,
    SIGNING_KEY,
    TEST_KID,
    TEST_SUPABASE_URL,
    auth_headers,
    jwks_document,
    make_token,
)

ME = "/api/v1/auth/me"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    # Unimplemented operations raise NotImplementedError before touching the database.
    app.state.infrascan_service = DatabaseInfraScanService(lambda: None, queue=None)
    return TestClient(app)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_unauthorized(response, code: str = "INVALID_TOKEN") -> None:
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == code


# --- Token verification ---


def test_valid_token_returns_principal(client: TestClient) -> None:
    response = client.get(ME, headers=auth_headers(CITIZEN_A))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(CITIZEN_A),
        "role": "citizen",
        "municipality_id": None,
    }


def test_missing_token_is_401(client: TestClient) -> None:
    assert_unauthorized(client.get(ME), "AUTHENTICATION_REQUIRED")


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer not-a-jwt",
        "Bearer a.b.c",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "Bearer two tokens",
        f"Token {make_token()}",
    ],
)
def test_malformed_token_is_401(client: TestClient, authorization: str) -> None:
    assert_unauthorized(client.get(ME, headers={"Authorization": authorization}))


def test_bearer_scheme_is_case_insensitive(client: TestClient) -> None:
    response = client.get(ME, headers={"Authorization": f"bearer {make_token()}"})
    assert response.status_code == 200


def test_invalid_signature_is_401(client: TestClient) -> None:
    forged = make_token(key=OTHER_KEY, role="developer")
    assert_unauthorized(client.get(ME, headers=bearer(forged)))


def test_tampered_payload_is_401(client: TestClient) -> None:
    header, _, signature = make_token().split(".")
    _, payload, _ = make_token(role="developer").split(".")
    assert_unauthorized(client.get(ME, headers=bearer(f"{header}.{payload}.{signature}")))


def test_expired_token_is_401(client: TestClient) -> None:
    assert_unauthorized(client.get(ME, headers=auth_headers(expires_in=-120)))


def test_wrong_issuer_is_401(client: TestClient) -> None:
    other_project = "https://another-project.supabase.co/auth/v1"
    assert_unauthorized(client.get(ME, headers=auth_headers(issuer=other_project)))


def test_wrong_audience_is_401(client: TestClient) -> None:
    assert_unauthorized(client.get(ME, headers=auth_headers(audience="anon")))


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(
            make_token(key="a-shared-secret-of-sufficient-length-32b", algorithm="HS256"),
            id="HS256",
        ),
        pytest.param(
            jwt.encode(
                {"sub": str(CITIZEN_A), "aud": "authenticated", "exp": 9_999_999_999},
                None,
                algorithm="none",
                headers={"kid": TEST_KID},
            ),
            id="none",
        ),
        pytest.param(
            make_token(key=ec.generate_private_key(ec.SECP384R1()), algorithm="ES384"),
            id="ES384",
        ),
    ],
)
def test_non_es256_algorithm_is_401(client: TestClient, token: str) -> None:
    assert_unauthorized(client.get(ME, headers=bearer(token)))


def test_es256_header_with_hmac_signature_is_401(client: TestClient) -> None:
    """The header cannot pick the verification algorithm (algorithm confusion)."""
    header, payload, _ = make_token().split(".")
    hmac_token = make_token(key="a-shared-secret-of-sufficient-length-32b", algorithm="HS256")
    _, _, hmac_signature = hmac_token.split(".")
    assert_unauthorized(client.get(ME, headers=bearer(f"{header}.{payload}.{hmac_signature}")))


@pytest.mark.parametrize("sub", ["not-a-uuid", "", "12345"])
def test_invalid_uuid_subject_is_401(client: TestClient, sub: str) -> None:
    assert_unauthorized(client.get(ME, headers=auth_headers(sub)))


def test_missing_required_claims_are_401(client: TestClient) -> None:
    no_exp = jwt.encode(
        {
            "iss": f"{TEST_SUPABASE_URL}/auth/v1",
            "sub": str(CITIZEN_A),
            "aud": "authenticated",
            "iat": 1,
        },
        SIGNING_KEY,
        algorithm="ES256",
        headers={"kid": TEST_KID},
    )
    assert_unauthorized(client.get(ME, headers=bearer(no_exp)))


@pytest.mark.parametrize("kid", [None, "", "unknown-kid"])
def test_missing_or_unknown_kid_is_401(client: TestClient, kid: str | None) -> None:
    assert_unauthorized(client.get(ME, headers=auth_headers(kid=kid)))


def test_anonymous_supabase_session_is_401(client: TestClient) -> None:
    assert_unauthorized(client.get(ME, headers=auth_headers(extra={"is_anonymous": True})))


# --- Roles ---


def test_role_in_user_metadata_cannot_elevate(client: TestClient) -> None:
    headers = auth_headers(
        CITIZEN_A, user_metadata={"role": "developer", "municipality_id": "anaheim"}
    )
    assert client.get(ME, headers=headers).json() == {
        "id": str(CITIZEN_A),
        "role": "citizen",
        "municipality_id": None,
    }
    assert client.get("/api/v1/detections", headers=headers).status_code == 403


@pytest.mark.parametrize(
    "role",
    ["citizen", "maintenance_crew", "city_engineer", "municipal_administrator", "developer"],
)
def test_signed_app_metadata_role_is_honored(client: TestClient, role: str) -> None:
    headers = auth_headers(DEVELOPER, role=role, municipality_id="stanton")
    assert client.get(ME, headers=headers).json() == {
        "id": str(DEVELOPER),
        "role": role,
        "municipality_id": "stanton",
    }


@pytest.mark.parametrize(
    "app_metadata",
    [{}, {"role": "superuser"}, {"role": "authenticated"}, {"role": ["developer"]}, {"role": None}],
)
def test_missing_or_unsupported_role_is_citizen(client: TestClient, app_metadata: dict) -> None:
    response = client.get(ME, headers=auth_headers(app_metadata=app_metadata))
    assert response.json()["role"] == "citizen"


def test_user_from_claims_rejects_non_mapping_app_metadata() -> None:
    user = user_from_claims({"sub": str(CITIZEN_A), "app_metadata": "developer"})
    assert user == AuthenticatedUser(CITIZEN_A, "citizen", None)


@pytest.mark.parametrize(
    ("role", "unrestricted"),
    [
        ("citizen", False),
        ("maintenance_crew", False),
        ("city_engineer", False),
        ("municipal_administrator", True),
        ("developer", True),
    ],
)
def test_job_access_policy(role: str, unrestricted: bool) -> None:
    access = JobAccess.for_user(AuthenticatedUser(CREW_MEMBER, role))
    other = uuid4()

    assert access.unrestricted is unrestricted
    assert access.allows(CREW_MEMBER) is True
    assert access.allows(other) is unrestricted
    # Historical jobs without an owner: privileged roles only.
    assert access.allows(None) is unrestricted


# --- Global and administrative operations ---


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/detections"),
        ("GET", f"/api/v1/detections/{uuid4()}"),
        ("GET", f"/api/v1/detections/{uuid4()}/evidence"),
        ("GET", "/api/v1/analytics/overview"),
        ("GET", "/api/v1/analytics/road-conditions"),
        ("GET", "/api/v1/analytics/geographic"),
        ("GET", "/api/v1/analytics/damage-distribution"),
        ("GET", f"/api/v1/roads/{uuid4()}/issues"),
        ("POST", "/api/v1/inference/smoke-test"),
    ],
)
@pytest.mark.parametrize("role", [None, "maintenance_crew", "city_engineer"])
def test_global_operations_are_forbidden_to_owner_scoped_roles(
    client: TestClient, method: str, path: str, role: str | None
) -> None:
    response = client.request(method, path, headers=auth_headers(role=role))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", ["municipal_administrator", "developer"])
def test_global_operations_reach_the_service_for_privileged_roles(
    client: TestClient, role: str
) -> None:
    response = client.get("/api/v1/detections", headers=auth_headers(ADMIN, role=role))
    assert response.status_code == 501


def test_issue_writes_are_privileged(client: TestClient) -> None:
    issue_id = uuid4()
    body = {"title": "Pothole", "severity": "minor"}
    citizen = auth_headers()

    assert client.post("/api/v1/issues", json=body, headers=citizen).status_code == 403
    assert (
        client.patch(f"/api/v1/issues/{issue_id}", json={}, headers=citizen).status_code == 403
    )
    admin = auth_headers(ADMIN, role="municipal_administrator")
    assert client.post("/api/v1/issues", json=body, headers=admin).status_code == 501


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/inference/jobs"),
        ("POST", "/api/v1/inference/smoke-test"),
        ("GET", "/api/v1/jobs"),
        ("GET", f"/api/v1/jobs/{uuid4()}"),
        ("GET", f"/api/v1/jobs/{uuid4()}/detections"),
        ("GET", f"/api/v1/evidence/{uuid4()}"),
        ("GET", "/api/v1/issues"),
        ("POST", "/api/v1/issues"),
        ("GET", "/api/v1/issues/map"),
        ("GET", "/api/v1/issues/review-queue"),
        ("GET", f"/api/v1/issues/{uuid4()}"),
        ("PATCH", f"/api/v1/issues/{uuid4()}"),
        ("GET", f"/api/v1/issues/{uuid4()}/detections"),
        ("GET", "/api/v1/roads"),
        ("GET", f"/api/v1/roads/{uuid4()}"),
        ("GET", "/api/v1/detections"),
        ("GET", "/api/v1/analytics/overview"),
    ],
)
def test_every_api_route_requires_a_token(client: TestClient, method: str, path: str) -> None:
    assert_unauthorized(client.request(method, path), "AUTHENTICATION_REQUIRED")


def test_unauthenticated_upload_is_rejected_before_parsing(client: TestClient) -> None:
    response = client.post(
        "/api/v1/inference/jobs",
        content=b"not multipart",
        headers={"content-type": "text/plain"},
    )
    assert_unauthorized(response, "AUTHENTICATION_REQUIRED")


# --- Configuration and JWKS availability ---


def test_missing_supabase_url_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUPABASE_URL")

    response = client.get(ME, headers=auth_headers())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTHENTICATION_UNAVAILABLE"


@pytest.mark.parametrize(
    "url",
    ["ftp://x.supabase.co", "http://x.supabase.co", "https://x.supabase.co/rest/v1", "nonsense"],
)
def test_unusable_supabase_url_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("SUPABASE_URL", url)
    assert client.get(ME, headers=auth_headers()).status_code == 503


def test_supabase_url_trailing_slash_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPABASE_URL", f"{TEST_SUPABASE_URL}/")
    assert client.get(ME, headers=auth_headers()).status_code == 200


def test_jwks_outage_is_503_and_never_logs_the_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unreachable(url: str) -> dict:
        raise urllib.error.URLError("network is unreachable")

    monkeypatch.setattr(auth, "fetch_jwks", unreachable)
    token = make_token()

    with caplog.at_level(logging.DEBUG):
        response = client.get(ME, headers=bearer(token))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTHENTICATION_UNAVAILABLE"
    assert "www-authenticate" not in response.headers
    assert token not in caplog.text
    assert token.split(".")[2] not in caplog.text


def test_jwks_without_es256_keys_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "fetch_jwks", lambda url: {"keys": [{"kty": "oct", "k": "AA"}]})
    assert client.get(ME, headers=auth_headers()).status_code == 503


def test_jwks_is_fetched_once_and_cached(
    client: TestClient, supabase_test_jwks: list[str]
) -> None:
    for _ in range(3):
        assert client.get(ME, headers=auth_headers()).status_code == 200

    assert supabase_test_jwks == [f"{TEST_SUPABASE_URL}/auth/v1/.well-known/jwks.json"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_jwks_cache_refreshes_after_ttl_and_keeps_stale_keys_during_outage() -> None:
    clock = FakeClock()
    responses: list[object] = [jwks_document(), urllib.error.URLError("down")]
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    cache = auth.JwksCache("https://jwks", fetch, ttl=600, clock=clock)
    first = cache.get(TEST_KID)
    clock.now += 601
    assert cache.get(TEST_KID) is first
    assert len(calls) == 2


def test_jwks_cache_rate_limits_refetches_for_unknown_kids() -> None:
    clock = FakeClock()
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        return jwks_document()

    cache = auth.JwksCache("https://jwks", fetch, min_refresh_interval=30, clock=clock)
    cache.get(TEST_KID)
    for _ in range(5):
        with pytest.raises(auth.UnknownSigningKeyError):
            cache.get("rotated-kid")
    assert len(calls) == 1
    clock.now += 31
    with pytest.raises(auth.UnknownSigningKeyError):
        cache.get("rotated-kid")
    assert len(calls) == 2


def test_jwks_cache_reports_outage_when_it_has_no_keys() -> None:
    def fetch(url: str):
        raise TimeoutError

    with pytest.raises(auth.JwksUnavailableError):
        auth.JwksCache("https://jwks", fetch).get(TEST_KID)


# --- Unauthenticated infrastructure endpoint and CORS ---


def test_root_health_endpoint_stays_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import main

    with TestClient(main.app) as test_client:
        assert test_client.get("/test").json() == "Hello InfraScan"


def test_cors_allows_authorization_header_from_configured_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import main

    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    with TestClient(main.app) as test_client:
        allowed = test_client.options(
            ME, headers={**preflight, "Origin": "http://localhost:5173"}
        )
        denied = test_client.options(
            ME, headers={**preflight, "Origin": "https://evil.example"}
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-origin" not in denied.headers


def test_cors_allowed_origins_parsing() -> None:
    from main import cors_allowed_origins

    assert cors_allowed_origins(None) == ["http://localhost:5173"]
    assert cors_allowed_origins("  ") == ["http://localhost:5173"]
    assert cors_allowed_origins("https://app.example.org/, http://localhost:5173,") == [
        "https://app.example.org",
        "http://localhost:5173",
    ]
    with pytest.raises(RuntimeError):
        cors_allowed_origins("*")
