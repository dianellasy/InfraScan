from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, status


USER_ROLES = frozenset(
    {
        "municipal_administrator",
        "city_engineer",
        "maintenance_crew",
        "developer",
        "citizen",
    }
)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    role: str
    municipality_id: str | None


@lru_cache(maxsize=1)
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json",
        lifespan=300,
    )


def _claims_from_token(token: str) -> dict[str, Any]:
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()

    if not supabase_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )

    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm and algorithm.startswith("HS"):
            if not jwt_secret:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication signing secret is not configured",
                )
            claims = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256", "HS384", "HS512"],
                audience="authenticated",
                issuer=f"{supabase_url.rstrip('/')}/auth/v1",
            )
        else:
            signing_key = _jwks_client(supabase_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[algorithm] if algorithm else None,
                audience="authenticated",
                issuer=f"{supabase_url.rstrip('/')}/auth/v1",
            )
    except HTTPException:
        raise
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return claims


def require_authenticated(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthenticatedUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = _claims_from_token(authorization.removeprefix("Bearer ").strip())
    try:
        user_id = UUID(str(claims["sub"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token subject",
        ) from exc

    app_metadata = claims.get("app_metadata")
    if not isinstance(app_metadata, dict):
        app_metadata = {}
    role = app_metadata.get("role")
    if role not in USER_ROLES:
        role = "citizen"
    municipality_id = app_metadata.get("municipality_id")

    return AuthenticatedUser(
        id=user_id,
        role=str(role),
        municipality_id=(
            str(municipality_id) if municipality_id is not None else None
        ),
    )


def require_roles(*allowed_roles: str):
    def dependency(
        user: AuthenticatedUser = Depends(require_authenticated),
    ) -> AuthenticatedUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return dependency
