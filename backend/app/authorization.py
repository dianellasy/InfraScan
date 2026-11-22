"""Application roles and the job-ownership policy.

Identities come from Supabase Auth, which is a separate system from the
InfraScan PostgreSQL database: `InferenceJob.user_id` stores the Supabase user
UUID (the verified JWT `sub`) without a foreign key. Roles are read only from
the signed `app_metadata` claim, never from user-editable `user_metadata`.

Every role/ownership decision lives here so routes, services, and repositories
apply the same rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID


class Role(StrEnum):
    CITIZEN = "citizen"
    MAINTENANCE_CREW = "maintenance_crew"
    CITY_ENGINEER = "city_engineer"
    MUNICIPAL_ADMINISTRATOR = "municipal_administrator"
    DEVELOPER = "developer"


SUPPORTED_ROLES: frozenset[str] = frozenset(role.value for role in Role)
# Missing or unrecognized roles get the least privilege.
DEFAULT_ROLE = Role.CITIZEN

# May read every job, including historical jobs that have no owner. Also the
# only roles allowed to call global or administrative operations that cannot be
# scoped to one owner. Maintenance crews and city engineers stay owner-scoped
# until municipality-wide tenancy exists.
PRIVILEGED_ROLES: frozenset[str] = frozenset(
    {Role.MUNICIPAL_ADMINISTRATOR.value, Role.DEVELOPER.value}
)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    role: str
    municipality_id: str | None = None

    @property
    def is_privileged(self) -> bool:
        return self.role in PRIVILEGED_ROLES


@dataclass(frozen=True)
class JobAccess:
    """Which inference jobs, and therefore which detections and evidence, a caller may read.

    Unrestricted access includes historical jobs whose `user_id` is NULL;
    owner-scoped access never matches them.
    """

    unrestricted: bool
    owner_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.unrestricted == (self.owner_id is not None):
            raise ValueError("JobAccess is either unrestricted or scoped to one owner")

    @classmethod
    def everything(cls) -> JobAccess:
        return cls(unrestricted=True)

    @classmethod
    def owned_by(cls, owner_id: UUID) -> JobAccess:
        return cls(unrestricted=False, owner_id=owner_id)

    @classmethod
    def for_user(cls, user: AuthenticatedUser) -> JobAccess:
        return cls.everything() if user.is_privileged else cls.owned_by(user.id)

    def allows(self, job_owner_id: UUID | None) -> bool:
        return self.unrestricted or (
            job_owner_id is not None and job_owner_id == self.owner_id
        )


class InvalidClaimsError(ValueError):
    """Verified token claims do not identify an InfraScan user."""


def _municipality_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def user_from_claims(claims: Mapping[str, Any]) -> AuthenticatedUser:
    """Build the principal from already signature-verified Supabase claims."""
    subject = claims.get("sub")
    if not isinstance(subject, str):
        raise InvalidClaimsError("sub must be a UUID string")
    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise InvalidClaimsError("sub must be a UUID string") from exc

    app_metadata = claims.get("app_metadata")
    if not isinstance(app_metadata, Mapping):
        app_metadata = {}
    role = app_metadata.get("role")
    if not isinstance(role, str) or role not in SUPPORTED_ROLES:
        role = DEFAULT_ROLE.value
    return AuthenticatedUser(
        id=user_id,
        role=role,
        municipality_id=_municipality_id(app_metadata.get("municipality_id")),
    )
