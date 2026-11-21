"""Fixed settings for controlled prototype captures; no device lookup yet."""

from collections.abc import Mapping
from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class CameraProfile:
    """Intrinsics in upright image pixels, plus the prototype mounting setup.

    Assumes a pinhole camera, zero lens distortion, zero roll and a flat road.
    Positive pitch points the camera down. Height is above the road, in meters.
    Intrinsics apply to the full frame at the reference resolution.
    """

    name: str
    image_width: int
    image_height: int
    fx_px: float
    fy_px: float
    cx_px: float
    cy_px: float
    height_m: float
    pitch_down_deg: float = 0.0
    max_ground_distance_m: float = 100.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Camera profile must have a name")
        for value in (self.image_width, self.image_height):
            if type(value) is not int or value <= 0:
                raise ValueError("Calibration dimensions must be positive integers")
        values = (
            self.fx_px, self.fy_px, self.cx_px, self.cy_px, self.height_m,
            self.pitch_down_deg, self.max_ground_distance_m,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Camera settings must be finite")
        if min(self.fx_px, self.fy_px, self.height_m, self.max_ground_distance_m) <= 0:
            raise ValueError("Focal lengths, height and range must be positive")
        if not (0 <= self.cx_px <= self.image_width and 0 <= self.cy_px <= self.image_height):
            raise ValueError("Principal point must be inside the calibration image")
        if not -90 < self.pitch_down_deg < 90:
            raise ValueError("Pitch must be between -90 and 90 degrees")


# SYNTHETIC example values, not a calibration of the Galaxy Flip or any device.
# Replace these with measured values before testing geolocation accuracy.
SAMPLE_CAMERA_PROFILE = CameraProfile(
    name="synthetic-dashcam-v1",
    image_width=4032,
    image_height=3024,
    fx_px=3000.0,
    fy_px=3000.0,
    cx_px=2016.0,
    cy_px=1512.0,
    height_m=1.5,
    # A modest downward dashcam mount moves the road horizon above image
    # center, allowing distant road detections to intersect the ground plane.
    pitch_down_deg=15.0,
)

# The image submission API has no heading. Every prototype image capture is
# assumed to face true north (0 degrees; east is 90). This is not EXIF
# Orientation. Video frames use the heading from embedded GPS instead.
PROTOTYPE_HEADING_DEG = 0.0

# PROVISIONAL, NOT CALIBRATED. VIOFO documents a 140-degree field of view for
# the A119 Mini 2; a pinhole focal length of (half diagonal) / tan(70 deg) at
# 1920x1080 is about 401 px. The real lens is a distorting wide-angle lens
# whose horizontal/vertical intrinsics have not been measured, and mount
# height/pitch depend on the vehicle. Projected detection coordinates from
# this profile are approximations until real footage is calibrated.
A119_MINI2_DIAGONAL_FOV_DEG = 140.0
A119_MINI2_WIDTH = 1920
A119_MINI2_HEIGHT = 1080
_A119_FOCAL_PX = round(
    math.hypot(A119_MINI2_WIDTH, A119_MINI2_HEIGHT)
    / 2
    / math.tan(math.radians(A119_MINI2_DIAGONAL_FOV_DEG / 2)),
    1,
)


def _env_float(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


def a119_mini2_1080p_profile(environ: Mapping[str, str] = os.environ) -> CameraProfile:
    """The provisional A119 Mini 2 1920x1080 profile, with configurable mounting."""
    return CameraProfile(
        name="provisional-viofo-a119-mini2-1080p",
        image_width=A119_MINI2_WIDTH,
        image_height=A119_MINI2_HEIGHT,
        fx_px=_env_float(environ, "INFRASCAN_A119_FX_PX", _A119_FOCAL_PX),
        fy_px=_env_float(environ, "INFRASCAN_A119_FY_PX", _A119_FOCAL_PX),
        cx_px=A119_MINI2_WIDTH / 2,
        cy_px=A119_MINI2_HEIGHT / 2,
        height_m=_env_float(environ, "INFRASCAN_A119_MOUNT_HEIGHT_M", 1.3),
        pitch_down_deg=_env_float(environ, "INFRASCAN_A119_PITCH_DOWN_DEG", 10.0),
        max_ground_distance_m=_env_float(environ, "INFRASCAN_A119_MAX_GROUND_DISTANCE_M", 60.0),
    )
