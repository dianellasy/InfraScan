"""London Bridge geolocation proof of concept.

While `INFRASCAN_POC_GEOLOCATION` is enabled, every accepted submission is
treated as though it was captured at London Bridge by the sample camera
setup below, regardless of the GPS in the request metadata or image EXIF.
The resulting detection coordinates demonstrate the pipeline; they do not
establish where any image was actually taken.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.geolocation.camera import CameraSetup, DiagonalFovProfile
from app.geolocation.projection import CameraOrientation, ProjectionLimits

POC_GEOLOCATION_ENV = "INFRASCAN_POC_GEOLOCATION"
POC_GEOLOCATION_MODE = "poc_assumed"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}

# No measured Galaxy Z Flip7 calibration is checked into the project, so this
# is an estimate. Replace it with a `CalibratedProfile` (intrinsic matrix,
# distortion coefficients, and calibration image size) once one is measured.
GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED = DiagonalFovProfile(
    name="Samsung Galaxy Z Flip7 rear main/wide camera, 1x (ESTIMATED, not calibrated)",
    diagonal_fov_deg=85.0,
    reference="Samsung published 85 degree field of view for the Flip7 main camera",
    assumptions=(
        "The published 85 degree field of view is treated as the diagonal field of view.",
        "Square pixels (fx = fy).",
        "Principal point at the image center.",
        "Zero lens distortion.",
        "The analyzed image is the full, uncropped sensor field of view.",
        "Synthetic approximation inspired by Flip7 specifications, not verified calibration.",
    ),
)


@dataclass(frozen=True)
class SourceLocation:
    name: str
    latitude: float
    longitude: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "datum": "WGS84",
        }


@dataclass(frozen=True)
class PocGeolocationConfig:
    source: SourceLocation
    camera: CameraSetup
    note: str

    def provenance(
        self, request_latitude: float | None, request_longitude: float | None
    ) -> dict[str, Any]:
        """Job-level record explaining how detection coordinates were produced.

        The worker adds `image` and `intrinsics` once the oriented image size
        is known.
        """
        request_gps = None
        if request_latitude is not None and request_longitude is not None:
            request_gps = {
                "latitude": request_latitude,
                "longitude": request_longitude,
                "used": False,
            }
        return {
            "mode": POC_GEOLOCATION_MODE,
            "assumed": True,
            "note": self.note,
            "source": self.source.to_dict(),
            "camera": self.camera.to_dict(),
            "request_gps": request_gps,
            "image": None,
            "intrinsics": None,
        }


LONDON_BRIDGE_POC = PocGeolocationConfig(
    source=SourceLocation(
        name="London Bridge, London, UK", latitude=51.507921, longitude=-0.087700
    ),
    camera=CameraSetup(
        profile=GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED,
        camera_height_m=1.5,
        orientation=CameraOrientation(heading_deg=0.0, pitch_deg=20.0, roll_deg=0.0),
        limits=ProjectionLimits(max_ground_distance_m=50.0, min_depression_deg=1.0),
        orientation_assumed=True,
    ),
    note=(
        "Assumed POC geolocation: the image is treated as though captured at "
        "London Bridge by a Galaxy Z Flip7 main camera (estimated intrinsics) "
        "1.5 m above a flat road, heading 0 deg (north), pitched 20 deg down, "
        "roll 0 deg. Orientation values are demo assumptions, not measurements. "
        "Detection coordinates are demo estimates and do not establish the "
        "image's real capture location."
    ),
)


def poc_geolocation_enabled(environ: Mapping[str, str] | None = None) -> bool:
    raw = (os.environ if environ is None else environ).get(POC_GEOLOCATION_ENV, "")
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise RuntimeError(
        f"{POC_GEOLOCATION_ENV} must be one of 1/true/yes/on or 0/false/no/off"
    )


def configured_poc_geolocation(
    environ: Mapping[str, str] | None = None,
) -> PocGeolocationConfig | None:
    return LONDON_BRIDGE_POC if poc_geolocation_enabled(environ) else None
