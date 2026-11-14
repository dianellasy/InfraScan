"""Inverse projection mapping from image pixels to a flat road plane.

Pure Python (no numpy/OpenCV) so the API process can import it.

Conventions
-----------
Image
    Pixel coordinates ``(u, v)`` in the upright image the detector saw (EXIF
    orientation already applied), origin at the top-left corner, ``u`` to the
    right and ``v`` down, in pixels. Coordinates are continuous: pixel column
    ``i`` spans ``[i, i + 1)``, so the image center is ``(width / 2,
    height / 2)``. Detector boxes use the same system.
Camera
    OpenCV convention: ``x`` right, ``y`` down, ``z`` forward along the optical
    axis. A pixel back-projects to the ray ``(x_n, y_n, 1)`` where
    ``x_n = (u - cx) / fx`` and ``y_n = (v - cy) / fy`` after undistortion.
World
    Local east-north-up (ENU) tangent frame in meters. The origin is the point
    on the road plane directly below the camera; ``x`` east, ``y`` north,
    ``z`` up. The camera center is at ``(0, 0, camera_height_m)`` and the road
    is the plane ``z = 0``.
Orientation
    Degrees, applied to the camera as roll, then pitch, then heading:

    - ``heading_deg``: rotation about world up, clockwise from true north as
      seen from above (0 = optical axis points north, 90 = east).
    - ``pitch_deg``: tilt of the optical axis below horizontal. Positive
      tilts the camera down toward the road.
    - ``roll_deg``: rotation about the optical axis. Positive dips the
      camera's right side (clockwise as seen from behind the camera), so the
      horizon appears rotated counterclockwise in the image.

Distortion coefficients follow OpenCV's ``(k1, k2, p1, p2[, k3])`` model on
normalized coordinates.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

Vector3 = tuple[float, float, float]
Matrix3 = tuple[Vector3, Vector3, Vector3]

# WGS84 ellipsoid.
WGS84_A = 6_378_137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)

# Contact points may sit exactly on the image border; allow half a pixel of
# rounding before calling a point outside the image.
_IMAGE_BOUNDS_TOLERANCE_PX = 0.5
_UNDISTORT_MAX_ITERATIONS = 50
_UNDISTORT_TOLERANCE = 1e-12


class ProjectionStatus(StrEnum):
    """Outcome of projecting one detection onto the road plane.

    Only ``ESTIMATED`` carries a coordinate; every other status keeps the
    detection with a NULL location.
    """

    ESTIMATED = "estimated"
    ABOVE_HORIZON = "above_horizon"
    BEHIND_CAMERA = "behind_camera"
    UNSTABLE_NEAR_HORIZON = "unstable_near_horizon"
    OUTSIDE_IMAGE = "outside_image"
    NONFINITE = "nonfinite"
    UNDISTORTION_FAILED = "undistortion_failed"
    INTRINSICS_MISMATCH = "intrinsics_mismatch"


PROJECTION_STATUSES: tuple[str, ...] = tuple(s.value for s in ProjectionStatus)


class IntrinsicsMismatchError(ValueError):
    """Intrinsics cannot be mapped onto the analyzed image's dimensions."""


def _finite(*values: float) -> bool:
    return all(math.isfinite(v) for v in values)


@dataclass(frozen=True)
class PinholeIntrinsics:
    """Pinhole intrinsics for one specific oriented image size, in pixels."""

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int
    distortion: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not _finite(self.fx, self.fy, self.cx, self.cy, *self.distortion):
            raise ValueError("intrinsics must be finite")
        if self.fx <= 0 or self.fy <= 0:
            raise ValueError("focal lengths must be positive")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("image dimensions must be positive")
        if len(self.distortion) not in (0, 4, 5):
            raise ValueError("distortion must be (k1, k2, p1, p2[, k3]) or empty")

    @property
    def has_distortion(self) -> bool:
        return any(c != 0 for c in self.distortion)

    @property
    def matrix(self) -> Matrix3:
        return ((self.fx, 0.0, self.cx), (0.0, self.fy, self.cy), (0.0, 0.0, 1.0))

    def scaled_to(
        self, width: int, height: int, aspect_tolerance: float = 0.01
    ) -> PinholeIntrinsics:
        """Rescale for a uniformly resized copy of the calibration image.

        A different aspect ratio means the image was cropped or rotated
        relative to calibration; the crop offset is unknown, so refuse.
        """
        if width <= 0 or height <= 0:
            raise IntrinsicsMismatchError("image dimensions must be positive")
        sx = width / self.width
        sy = height / self.height
        if abs(sx - sy) > aspect_tolerance * max(sx, sy):
            raise IntrinsicsMismatchError(
                f"image {width}x{height} does not have the calibration aspect "
                f"ratio {self.width}x{self.height}; it may be cropped or rotated"
            )
        return PinholeIntrinsics(
            fx=self.fx * sx,
            fy=self.fy * sy,
            cx=self.cx * sx,
            cy=self.cy * sy,
            width=width,
            height=height,
            distortion=self.distortion,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "fx": self.fx,
            "fy": self.fy,
            "cx": self.cx,
            "cy": self.cy,
            "width": self.width,
            "height": self.height,
            "distortion": list(self.distortion),
        }


@dataclass(frozen=True)
class CameraOrientation:
    heading_deg: float
    pitch_deg: float
    roll_deg: float = 0.0

    def __post_init__(self) -> None:
        if not _finite(self.heading_deg, self.pitch_deg, self.roll_deg):
            raise ValueError("orientation angles must be finite")


@dataclass(frozen=True)
class ProjectionLimits:
    """Reject intersections whose distance is too sensitive to pixel error.

    Range error grows roughly with distance squared over camera height, so
    near the horizon a one-pixel error becomes meters. Both limits apply.
    """

    max_ground_distance_m: float = 50.0
    min_depression_deg: float = 1.0

    def __post_init__(self) -> None:
        if not _finite(self.max_ground_distance_m, self.min_depression_deg):
            raise ValueError("projection limits must be finite")
        if self.max_ground_distance_m <= 0 or not 0 <= self.min_depression_deg < 90:
            raise ValueError("projection limits are out of range")


@dataclass(frozen=True)
class GroundIntersection:
    status: ProjectionStatus
    east_m: float | None = None
    north_m: float | None = None
    ground_distance_m: float | None = None
    message: str | None = None


def _matmul(a: Matrix3, b: Matrix3) -> Matrix3:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )


def _matvec(m: Matrix3, v: Vector3) -> Vector3:
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))  # type: ignore[return-value]


def _transpose(m: Matrix3) -> Matrix3:
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))  # type: ignore[return-value]


def camera_to_world_rotation(orientation: CameraOrientation) -> Matrix3:
    """Rotation taking camera-frame vectors to ENU world vectors."""
    h = math.radians(orientation.heading_deg)
    p = math.radians(orientation.pitch_deg)
    r = math.radians(orientation.roll_deg)
    # Level camera facing north: x -> east, y (down) -> -up, z -> north.
    level_north = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    roll = ((math.cos(r), -math.sin(r), 0.0), (math.sin(r), math.cos(r), 0.0), (0.0, 0.0, 1.0))
    pitch = ((1.0, 0.0, 0.0), (0.0, math.cos(p), math.sin(p)), (0.0, -math.sin(p), math.cos(p)))
    heading = ((math.cos(h), math.sin(h), 0.0), (-math.sin(h), math.cos(h), 0.0), (0.0, 0.0, 1.0))
    return _matmul(heading, _matmul(level_north, _matmul(pitch, roll)))


def _distortion_terms(distortion: Sequence[float]) -> tuple[float, float, float, float, float]:
    k1, k2, p1, p2 = distortion[:4]
    k3 = distortion[4] if len(distortion) > 4 else 0.0
    return k1, k2, p1, p2, k3


def distort_normalized(
    x: float, y: float, distortion: Sequence[float]
) -> tuple[float, float]:
    """Apply OpenCV's radial/tangential model to an ideal normalized point."""
    if not any(distortion):
        return x, y
    k1, k2, p1, p2, k3 = _distortion_terms(distortion)
    r2 = x * x + y * y
    radial = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return xd, yd


def undistort_normalized(
    xd: float, yd: float, distortion: Sequence[float]
) -> tuple[float, float] | None:
    """Invert `distort_normalized` by fixed-point iteration (as cv2.undistortPoints).

    Returns None when the iteration diverges or does not converge.
    """
    if not any(distortion):
        return xd, yd
    k1, k2, p1, p2, k3 = _distortion_terms(distortion)
    x, y = xd, yd
    for _ in range(_UNDISTORT_MAX_ITERATIONS):
        r2 = x * x + y * y
        radial = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
        if not math.isfinite(radial) or radial <= 0:
            return None
        dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        nx, ny = (xd - dx) / radial, (yd - dy) / radial
        if not _finite(nx, ny):
            return None
        if abs(nx - x) < _UNDISTORT_TOLERANCE and abs(ny - y) < _UNDISTORT_TOLERANCE:
            return nx, ny
        x, y = nx, ny
    return None


def bbox_contact_point(bbox: Sequence[float]) -> tuple[float, float]:
    """Approximate road contact point: bottom-center of an `[x1, y1, x2, y2]` box.

    Road damage lies on the road surface, so the box's lowest edge is the part
    closest to the camera and touching the plane. For a pothole or crack the
    true footprint spans the whole box; this picks its nearest point.
    """
    x1, _y1, x2, y2 = bbox
    return (x1 + x2) / 2, y2


def intersect_ground_plane(
    direction_world: Vector3, camera_height_m: float
) -> tuple[ProjectionStatus, float | None]:
    """Intersect the ray `(0, 0, h) + t * d` with `z = 0`; return status and `t`."""
    dz = direction_world[2]
    if not _finite(*direction_world, camera_height_m):
        return ProjectionStatus.NONFINITE, None
    if dz == 0:
        return ProjectionStatus.ABOVE_HORIZON, None
    t = -camera_height_m / dz
    if not math.isfinite(t):
        return ProjectionStatus.NONFINITE, None
    if t <= 0:
        # For a camera above the road, an upward ray's line meets the plane
        # only behind the camera: that is "above the horizon". A descending
        # ray with t <= 0 means the camera is at or below the plane.
        if dz > 0:
            return ProjectionStatus.ABOVE_HORIZON, None
        return ProjectionStatus.BEHIND_CAMERA, None
    return ProjectionStatus.ESTIMATED, t


def project_pixel_to_ground(
    u: float,
    v: float,
    intrinsics: PinholeIntrinsics,
    orientation: CameraOrientation,
    camera_height_m: float,
    limits: ProjectionLimits = ProjectionLimits(),
) -> GroundIntersection:
    """Back-project one pixel and intersect it with the flat road plane."""
    if not _finite(u, v):
        return GroundIntersection(ProjectionStatus.NONFINITE, message="pixel is not finite")
    tol = _IMAGE_BOUNDS_TOLERANCE_PX
    if not (-tol <= u <= intrinsics.width + tol and -tol <= v <= intrinsics.height + tol):
        return GroundIntersection(
            ProjectionStatus.OUTSIDE_IMAGE,
            message=(
                f"contact point ({u:.1f}, {v:.1f}) is outside the "
                f"{intrinsics.width}x{intrinsics.height} image"
            ),
        )

    xd = (u - intrinsics.cx) / intrinsics.fx
    yd = (v - intrinsics.cy) / intrinsics.fy
    undistorted = undistort_normalized(xd, yd, intrinsics.distortion)
    if undistorted is None:
        return GroundIntersection(
            ProjectionStatus.UNDISTORTION_FAILED,
            message="lens undistortion did not converge for this pixel",
        )

    ray_world = _matvec(camera_to_world_rotation(orientation), (*undistorted, 1.0))
    status, t = intersect_ground_plane(ray_world, camera_height_m)
    if status is ProjectionStatus.ABOVE_HORIZON:
        return GroundIntersection(
            status, message="ray is at or above the horizon and never reaches the road"
        )
    if status is ProjectionStatus.BEHIND_CAMERA:
        return GroundIntersection(status, message="road intersection is behind the camera")
    if status is not ProjectionStatus.ESTIMATED or t is None:
        return GroundIntersection(status, message="ray or intersection is not finite")

    east = t * ray_world[0]
    north = t * ray_world[1]
    distance = math.hypot(east, north)
    if not _finite(east, north, distance):
        return GroundIntersection(ProjectionStatus.NONFINITE, message="intersection is not finite")

    norm = math.sqrt(sum(c * c for c in ray_world))
    depression_deg = math.degrees(math.asin(min(1.0, -ray_world[2] / norm)))
    if (
        depression_deg < limits.min_depression_deg
        or distance > limits.max_ground_distance_m
    ):
        return GroundIntersection(
            ProjectionStatus.UNSTABLE_NEAR_HORIZON,
            ground_distance_m=distance,
            message=(
                f"intersection {distance:.1f} m away at {depression_deg:.2f} deg "
                f"below horizontal exceeds the stable range "
                f"(<= {limits.max_ground_distance_m:g} m, "
                f">= {limits.min_depression_deg:g} deg)"
            ),
        )
    return GroundIntersection(
        ProjectionStatus.ESTIMATED, east_m=east, north_m=north, ground_distance_m=distance
    )


def project_ground_to_pixel(
    east_m: float,
    north_m: float,
    intrinsics: PinholeIntrinsics,
    orientation: CameraOrientation,
    camera_height_m: float,
) -> tuple[float, float] | None:
    """Forward projection of a road point; None if it is behind the camera."""
    world_to_camera = _transpose(camera_to_world_rotation(orientation))
    xc, yc, zc = _matvec(world_to_camera, (east_m, north_m, -camera_height_m))
    if zc <= 0:
        return None
    xd, yd = distort_normalized(xc / zc, yc / zc, intrinsics.distortion)
    return intrinsics.fx * xd + intrinsics.cx, intrinsics.fy * yd + intrinsics.cy


def offset_to_wgs84(
    latitude: float, longitude: float, east_m: float, north_m: float
) -> tuple[float, float]:
    """Move a WGS84 point by a local east/north offset in meters.

    Uses the ellipsoid's meridional (M) and prime-vertical (N) radii of
    curvature at the source latitude. For offsets of tens of meters the
    error is far below a millimeter.
    """
    if not _finite(latitude, longitude, east_m, north_m):
        raise ValueError("coordinates and offsets must be finite")
    phi = math.radians(latitude)
    sin_phi = math.sin(phi)
    w = 1 - WGS84_E2 * sin_phi * sin_phi
    meridional = WGS84_A * (1 - WGS84_E2) / (w * math.sqrt(w))
    prime_vertical = WGS84_A / math.sqrt(w)
    cos_phi = math.cos(phi)
    if abs(cos_phi) < 1e-9:
        raise ValueError("east offsets are undefined at the poles")
    new_lat = latitude + math.degrees(north_m / meridional)
    new_lon = longitude + math.degrees(east_m / (prime_vertical * cos_phi))
    new_lon = (new_lon + 180.0) % 360.0 - 180.0
    if not -90 <= new_lat <= 90:
        raise ValueError("offset moves the point past a pole")
    return new_lat, new_lon
