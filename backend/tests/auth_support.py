"""Supabase-style ES256 access tokens signed with a throwaway test key.

`conftest.py` serves `jwks_document()` in place of the real JWKS download, so the
production verifier checks real signatures without contacting Supabase.
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

TEST_SUPABASE_URL = "https://infrascan-test.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"
TEST_KID = "infrascan-test-es256"

SIGNING_KEY = ec.generate_private_key(ec.SECP256R1())
# Same curve and algorithm, but absent from the JWKS: signatures must not verify.
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())

CITIZEN_A = UUID("0a0a0a0a-0000-4000-8000-00000000000a")
CITIZEN_B = UUID("0b0b0b0b-0000-4000-8000-00000000000b")
CREW_MEMBER = UUID("0c0c0c0c-0000-4000-8000-00000000000c")
ADMIN = UUID("0d0d0d0d-0000-4000-8000-00000000000d")
DEVELOPER = UUID("0e0e0e0e-0000-4000-8000-00000000000e")


def jwks_document() -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(SIGNING_KEY.public_key()))
    return {"keys": [{**jwk, "kid": TEST_KID, "alg": "ES256", "use": "sig"}]}


def make_token(
    sub: UUID | str = CITIZEN_A,
    *,
    role: str | None = None,
    municipality_id: str | None = None,
    app_metadata: dict[str, Any] | None = None,
    user_metadata: dict[str, Any] | None = None,
    expires_in: int = 3600,
    issuer: str = TEST_ISSUER,
    audience: str = "authenticated",
    kid: str | None = TEST_KID,
    key: Any = SIGNING_KEY,
    algorithm: str = "ES256",
    extra: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    if app_metadata is None:
        app_metadata = {"provider": "email", "providers": ["email"]}
        if role is not None:
            app_metadata["role"] = role
        if municipality_id is not None:
            app_metadata["municipality_id"] = municipality_id
    claims = {
        "iss": issuer,
        "sub": str(sub),
        "aud": audience,
        "iat": now,
        "exp": now + expires_in,
        "role": "authenticated",
        "email": f"{sub}@example.test",
        "app_metadata": app_metadata,
        "user_metadata": user_metadata or {},
        **(extra or {}),
    }
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, key, algorithm=algorithm, headers=headers)


def auth_headers(sub: UUID | str = CITIZEN_A, **token_options: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(sub, **token_options)}"}


def citizen_a() -> dict[str, str]:
    return auth_headers(CITIZEN_A)


def citizen_b() -> dict[str, str]:
    return auth_headers(CITIZEN_B)


def administrator() -> dict[str, str]:
    return auth_headers(ADMIN, role="municipal_administrator")


def developer() -> dict[str, str]:
    return auth_headers(DEVELOPER, role="developer")
