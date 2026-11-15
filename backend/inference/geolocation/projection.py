"""Pinhole ray/flat-road intersection and a spherical Earth GPS offset."""

from collections.abc import Sequence
from dataclasses import dataclass
import math

from inference.geolocation.camera_profiles import CameraProfile

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class GroundOffset:
    right_m: float
    forward_m: float


def project_to_ground(
    bounding_box: Sequence[float],
    image_width: int,
    image_height: int,
    profile: CameraProfile,
) -> GroundOffset | None:
    """Project the box's bottom center onto the road below the camera.

    Coordinates are in the upright prediction source, not YOLO's letterbox.
    Scale intrinsics for full-frame resizing (including independent x/y
    scaling); crops, digital zoom and changing lenses are not modeled.
    Unusable boxes/rays return None so the detection can still be retained.
    """
    if type(image_width) is not int or type(image_height) is not int:
        raise ValueError("Prediction dimensions must be integers")
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Prediction dimensions must be positive")
    if len(bounding_box) != 4 or not all(math.isfinite(v) for v in bounding_box):
        return None
    x1, y1, x2, y2 = bounding_box
    if not (0 <= x1 < x2 <= image_width and 0 <= y1 < y2 <= image_height):
        return None

    sx = image_width / profile.image_width
    sy = image_height / profile.image_height
    u, v = (x1 + x2) / 2, y2
    right = (u - profile.cx_px * sx) / (profile.fx_px * sx)
    down = (v - profile.cy_px * sy) / (profile.fy_px * sy)
    pitch = math.radians(profile.pitch_down_deg)
    # Camera ray [right, down, 1], rotated into a level ground frame.
    forward = math.cos(pitch) - down * math.sin(pitch)
    down = math.sin(pitch) + down * math.cos(pitch)
    if down <= 1e-9 or forward <= 0:
        return None

    scale = profile.height_m / down
    offset = GroundOffset(right * scale, forward * scale)
    if math.hypot(offset.right_m, offset.forward_m) > profile.max_ground_distance_m:
        return None
    return offset


def offset_to_gps(
    offset: GroundOffset,
    latitude: float,
    longitude: float,
    heading_deg: float,
) -> dict[str, float]:
    """Rotate right/forward by true heading, then move over a spherical Earth.

    Heading is clockwise from true north. This is a short-range prototype
    approximation, not an ellipsoidal survey calculation.
    """
    if not all(math.isfinite(v) for v in (
        latitude, longitude, heading_deg, offset.right_m, offset.forward_m,
    )):
        raise ValueError("GPS, heading and ground offset must be finite")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Source GPS coordinates are out of range")
    heading = math.radians(heading_deg % 360)
    east = offset.forward_m * math.sin(heading) + offset.right_m * math.cos(heading)
    north = offset.forward_m * math.cos(heading) - offset.right_m * math.sin(heading)
    distance = math.hypot(east, north)
    if distance == 0:
        return {"latitude": latitude, "longitude": longitude}

    lat, lon = math.radians(latitude), math.radians(longitude)
    angle = distance / EARTH_RADIUS_M
    # Unit vectors at the source: position, local north, local east. Using
    # vectors avoids division by cos(latitude) near the poles.
    p = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
    n = (-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat))
    e = (-math.sin(lon), math.cos(lon), 0.0)
    x, y, z = (
        math.cos(angle) * p[i]
        + math.sin(angle) * (north * n[i] + east * e[i]) / distance
        for i in range(3)
    )
    return {
        "latitude": math.degrees(math.atan2(z, math.hypot(x, y))),
        "longitude": (math.degrees(math.atan2(y, x)) + 180) % 360 - 180,
    }


def geolocate_detection(
    bounding_box: Sequence[float],
    image_width: int,
    image_height: int,
    *,
    latitude: float,
    longitude: float,
    heading_deg: float,
    profile: CameraProfile,
) -> dict[str, float] | None:
    offset = project_to_ground(bounding_box, image_width, image_height, profile)
    return offset_to_gps(offset, latitude, longitude, heading_deg) if offset else None
