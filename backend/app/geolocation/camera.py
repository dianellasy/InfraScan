"""Camera profiles and mounting setup, serializable for job provenance."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.geolocation.projection import (
    CameraOrientation,
    PinholeIntrinsics,
    ProjectionLimits,
)


@dataclass(frozen=True)
class DiagonalFovProfile:
    """Synthetic intrinsics from a published diagonal field of view.

    Evaluated at each analyzed image's own oriented dimensions:
    ``f = hypot(w, h) / (2 tan(fov / 2))``, ``fx = fy = f``, principal point
    at the image center, zero distortion. This treats the analyzed image as
    the full, uncropped field of view.
    """

    kind: ClassVar[str] = "diagonal_fov_estimate"

    name: str
    diagonal_fov_deg: float
    reference: str
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 < self.diagonal_fov_deg < 180:
            raise ValueError("diagonal field of view must be between 0 and 180 degrees")

    @property
    def calibrated(self) -> bool:
        return False

    def intrinsics_for(self, width: int, height: int) -> PinholeIntrinsics:
        focal = math.hypot(width, height) / (
            2 * math.tan(math.radians(self.diagonal_fov_deg) / 2)
        )
        return PinholeIntrinsics(
            fx=focal, fy=focal, cx=width / 2, cy=height / 2, width=width, height=height
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "calibrated": False,
            "diagonal_fov_deg": self.diagonal_fov_deg,
            "reference": self.reference,
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class CalibratedProfile:
    """Measured intrinsics and distortion at the calibration image size.

    Uniformly resized images are supported; cropped or rotated images are
    rejected because their offset from the calibration frame is unknown.
    """

    kind: ClassVar[str] = "calibrated"

    name: str
    intrinsics: PinholeIntrinsics
    reference: str
    notes: tuple[str, ...] = field(default=())

    @property
    def calibrated(self) -> bool:
        return True

    def intrinsics_for(self, width: int, height: int) -> PinholeIntrinsics:
        return self.intrinsics.scaled_to(width, height)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "calibrated": True,
            "intrinsics": self.intrinsics.as_dict(),
            "reference": self.reference,
            "notes": list(self.notes),
        }


CameraProfile = DiagonalFovProfile | CalibratedProfile


def camera_profile_from_dict(data: Mapping[str, Any]) -> CameraProfile:
    kind = data.get("kind")
    if kind == DiagonalFovProfile.kind:
        return DiagonalFovProfile(
            name=str(data["name"]),
            diagonal_fov_deg=float(data["diagonal_fov_deg"]),
            reference=str(data.get("reference", "")),
            assumptions=tuple(str(a) for a in data.get("assumptions", ())),
        )
    if kind == CalibratedProfile.kind:
        k = data["intrinsics"]
        return CalibratedProfile(
            name=str(data["name"]),
            intrinsics=PinholeIntrinsics(
                fx=float(k["fx"]),
                fy=float(k["fy"]),
                cx=float(k["cx"]),
                cy=float(k["cy"]),
                width=int(k["width"]),
                height=int(k["height"]),
                distortion=tuple(float(c) for c in k.get("distortion", ())),
            ),
            reference=str(data.get("reference", "")),
            notes=tuple(str(n) for n in data.get("notes", ())),
        )
    raise ValueError(f"unknown camera profile kind: {kind!r}")


@dataclass(frozen=True)
class CameraSetup:
    """Everything besides source GPS needed to geolocate a detection."""

    profile: CameraProfile
    camera_height_m: float
    orientation: CameraOrientation
    limits: ProjectionLimits = ProjectionLimits()
    orientation_assumed: bool = True

    CONTACT_POINT: ClassVar[str] = "bbox_bottom_center"
    GROUND_PLANE: ClassVar[str] = "flat"

    def __post_init__(self) -> None:
        if not math.isfinite(self.camera_height_m) or self.camera_height_m <= 0:
            raise ValueError("camera height must be a positive number of meters")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "height_m": self.camera_height_m,
            "orientation": {
                "heading_deg": self.orientation.heading_deg,
                "pitch_deg": self.orientation.pitch_deg,
                "roll_deg": self.orientation.roll_deg,
                "assumed": self.orientation_assumed,
            },
            "ground_plane": self.GROUND_PLANE,
            "contact_point": self.CONTACT_POINT,
            "limits": {
                "max_ground_distance_m": self.limits.max_ground_distance_m,
                "min_depression_deg": self.limits.min_depression_deg,
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CameraSetup:
        orientation = data["orientation"]
        limits = data.get("limits") or {}
        if data.get("ground_plane", cls.GROUND_PLANE) != cls.GROUND_PLANE:
            raise ValueError("only a flat ground plane is supported")
        if data.get("contact_point", cls.CONTACT_POINT) != cls.CONTACT_POINT:
            raise ValueError("only the bottom-center contact point is supported")
        return cls(
            profile=camera_profile_from_dict(data["profile"]),
            camera_height_m=float(data["height_m"]),
            orientation=CameraOrientation(
                heading_deg=float(orientation["heading_deg"]),
                pitch_deg=float(orientation["pitch_deg"]),
                roll_deg=float(orientation.get("roll_deg", 0.0)),
            ),
            limits=ProjectionLimits(**{k: float(v) for k, v in limits.items()}),
            orientation_assumed=bool(orientation.get("assumed", True)),
        )
