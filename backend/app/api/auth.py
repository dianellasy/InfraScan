"""Supabase access-token verification and the FastAPI authentication dependencies.

Supabase is used only for authentication. Access tokens are ES256 JWTs that are
verified locally against the project's public JWKS
(`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`); the backend never needs a
Supabase secret, service-role key, or JWT secret, and never connects to the
Supabase database. Access tokens must never be logged.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from typing import Annotated, Any
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.authorization import (
    PRIVILEGED_ROLES,
    AuthenticatedUser,
    InvalidClaimsError,
    JobAccess,
    user_from_claims,
)

logger = logging.getLogger(__name__)

ALGORITHM = "ES256"
AUDIENCE = "authenticated"
JWKS_PATH = "/auth/v1/.well-known/jwks.json"
JWKS_CACHE_SECONDS = 600.0
# Bounds refetches triggered by tokens carrying an unknown `kid`.
JWKS_MIN_REFRESH_SECONDS = 30.0
JWKS_TIMEOUT_SECONDS = 5.0
MAX_JWKS_BYTES = 64 * 1024
# Tolerates small clock differences between Supabase and this server.
CLOCK_SKEW_SECONDS = 30
REQUIRED_CLAIMS = ("exp", "iat", "sub", "aud", "iss")

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


class AuthenticationError(HTTPException):
    """401: the request has no usable bearer token."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, message, _BEARER_CHALLENGE)
        self.code = code


class AuthenticationUnavailableError(HTTPException):
    """503: tokens cannot be verified right now (configuration or JWKS outage)."""

    def __init__(self, message: str = "Authentication is temporarily unavailable.") -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, message)
        self.code = "AUTHENTICATION_UNAVAILABLE"


class PermissionDeniedError(HTTPException):
    """403: the caller's role may not use this operation."""

    def __init__(self) -> None:
        super().__init__(
            status.HTTP_403_FORBIDDEN, "Your role is not allowed to perform this operation."
        )
        self.code = "FORBIDDEN"


def _invalid_token() -> AuthenticationError:
    return AuthenticationError("INVALID_TOKEN", "The access token is invalid or expired.")


class JwksUnavailableError(Exception):
    """The JWKS could not be fetched or contained no usable ES256 keys."""


class UnknownSigningKeyError(LookupError):
    """No published signing key matches the token's `kid`."""


def fetch_jwks(url: str) -> Any:
    """Download and decode the JWKS document. Tests replace this network boundary."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=JWKS_TIMEOUT_SECONDS) as response:
        body = response.read(MAX_JWKS_BYTES + 1)
    if len(body) > MAX_JWKS_BYTES:
        raise ValueError("JWKS document is too large")
    return json.loads(body)


def _es256_keys(document: Any) -> dict[str, jwt.PyJWK]:
    entries = document.get("keys") if isinstance(document, Mapping) else None
    keys: dict[str, jwt.PyJWK] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        kid = entry.get("kid")
        if (
            not isinstance(kid, str)
            or not kid
            or entry.get("kty") != "EC"
            or entry.get("crv") != "P-256"
            or entry.get("alg", ALGORITHM) != ALGORITHM
            or entry.get("use", "sig") != "sig"
        ):
            continue
        try:
            keys[kid] = jwt.PyJWK(dict(entry), algorithm=ALGORITHM)
        except jwt.PyJWTError:
            continue
    if not keys:
        raise JwksUnavailableError("the JWKS has no usable ES256 signing keys")
    return keys


class JwksCache:
    """Thread-safe JWKS cache with a TTL and a rate-limited refresh for unknown key IDs.

    After a failed refresh, keys already in the cache keep verifying tokens so
    a brief JWKS outage does not sign everyone out.
    """

    def __init__(
        self,
        url: str,
        fetch: Callable[[str], Any],
        *,
        ttl: float = JWKS_CACHE_SECONDS,
        min_refresh_interval: float = JWKS_MIN_REFRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._fetch = fetch
        self._ttl = ttl
        self._min_refresh_interval = min_refresh_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None

    def _refresh(self, now: float) -> bool:
        self._attempted_at = now
        try:
            self._keys = _es256_keys(self._fetch(self._url))
        except Exception:
            logger.warning("Could not refresh the Supabase JWKS from %s", self._url, exc_info=True)
            return False
        self._fetched_at = now
        return True

    def get(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            now = self._clock()
            refreshed: bool | None = None
            if self._fetched_at is None or now - self._fetched_at >= self._ttl:
                refreshed = self._refresh(now)
            elif kid not in self._keys and (
                self._attempted_at is None
                or now - self._attempted_at >= self._min_refresh_interval
            ):
                refreshed = self._refresh(now)
            key = self._keys.get(kid)
            if key is not None:
                return key
            if refreshed is False:
                raise JwksUnavailableError(self._url)
            raise UnknownSigningKeyError(kid)


class SupabaseTokenVerifier:
    def __init__(self, supabase_url: str, keys: JwksCache) -> None:
        self.issuer = f"{supabase_url}/auth/v1"
        self._keys = keys

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc
        # The accepted algorithm is fixed here; the header is only checked against it.
        if header.get("alg") != ALGORITHM:
            raise _invalid_token()
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise _invalid_token()
        try:
            signing_key = self._keys.get(kid)
        except UnknownSigningKeyError as exc:
            raise _invalid_token() from exc
        except JwksUnavailableError as exc:
            raise AuthenticationUnavailableError() from exc

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=self.issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": list(REQUIRED_CLAIMS)},
            )
        except jwt.PyJWTError as exc:
            raise _invalid_token() from exc
        # Only email/password accounts are supported; anonymous sign-ins are not users.
        if claims.get("is_anonymous") is True:
            raise _invalid_token()
        try:
            return user_from_claims(claims)
        except InvalidClaimsError as exc:
            raise _invalid_token() from exc


def normalize_supabase_url(raw_url: str) -> str | None:
    """`https://<project-ref>.supabase.co` without a trailing slash, or None if unusable."""
    url = raw_url.strip().rstrip("/")
    parts = urlsplit(url)
    local = parts.hostname in {"localhost", "127.0.0.1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        return None
    if not parts.hostname or parts.query or parts.fragment or parts.path not in {"", "/"}:
        return None
    return url


_verifiers: dict[str, SupabaseTokenVerifier] = {}
_verifiers_lock = threading.Lock()


def reset_token_verifiers() -> None:
    """Drop cached verifiers and their JWKS (used by tests and after config changes)."""
    with _verifiers_lock:
        _verifiers.clear()


def get_token_verifier() -> SupabaseTokenVerifier:
    supabase_url = normalize_supabase_url(os.environ.get("SUPABASE_URL", ""))
    if supabase_url is None:
        logger.error("SUPABASE_URL is missing or invalid; cannot verify access tokens")
        raise AuthenticationUnavailableError("Authentication is not configured.")
    with _verifiers_lock:
        verifier = _verifiers.get(supabase_url)
        if verifier is None:
            # Looked up at call time so tests can replace the network boundary.
            keys = JwksCache(f"{supabase_url}{JWKS_PATH}", lambda url: fetch_jwks(url))
            verifier = _verifiers[supabase_url] = SupabaseTokenVerifier(supabase_url, keys)
    return verifier


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise AuthenticationError("AUTHENTICATION_REQUIRED", "Sign in to use this endpoint.")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or " " in token:
        raise _invalid_token()
    return token


# Synchronous so FastAPI runs it in the thread pool: a JWKS refresh may block on the network.
def require_user(request: Request) -> AuthenticatedUser:
    token = _bearer_token(request)
    return get_token_verifier().verify(token)


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]


def require_privileged_user(user: CurrentUser) -> AuthenticatedUser:
    """For global or administrative operations that cannot be scoped to one owner."""
    if user.role not in PRIVILEGED_ROLES:
        raise PermissionDeniedError()
    return user


PrivilegedUser = Annotated[AuthenticatedUser, Depends(require_privileged_user)]


def get_job_access(user: CurrentUser) -> JobAccess:
    return JobAccess.for_user(user)


CurrentJobAccess = Annotated[JobAccess, Depends(get_job_access)]
