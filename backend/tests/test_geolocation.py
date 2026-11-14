"""Inverse projection mapping geometry (no database, no model)."""

from __future__ import annotations

import math

import pytest

from app.geolocation import (
    LONDON_BRIDGE_POC,
    CalibratedProfile,
    CameraOrientation,
    CameraSetup,
    GroundPlaneGeolocator,
    PinholeIntrinsics,
    ProjectionLimits,
    ProjectionStatus,
    configured_poc_geolocation,
)
from app.geolocation.poc import GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED
from app.geolocation.projection import (
    IntrinsicsMismatchError,
    bbox_contact_point,
    distort_normalized,
    intersect_ground_plane,
    offset_to_wgs84,
    project_ground_to_pixel,
    project_pixel_to_ground,
    undistort_normalized,
)

W, H = 886, 595  # multi.png
HEIGHT_M = 1.5
PITCH = 20.0
SAMPLE = CameraOrientation(heading_deg=0.0, pitch_deg=PITCH, roll_deg=0.0)
ESTIMATED = GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED.intrinsics_for(W, H)
DISTORTED = PinholeIntrinsics(
    fx=3100.0,
    fy=3090.0,
    cx=2010.0,
    cy=1490.0,
    width=4000,
    height=3000,
    distortion=(0.12, -0.25, 0.001, -0.0015, 0.08),
)


def ground(u: float, v: float, **kwargs):
    params = {
        "intrinsics": ESTIMATED,
        "orientation": SAMPLE,
        "camera_height_m": HEIGHT_M,
        **kwargs,
    }
    return project_pixel_to_ground(u, v, **params)


def horizon_row(intrinsics: PinholeIntrinsics, pitch_deg: float) -> float:
    return intrinsics.cy - intrinsics.fy * math.tan(math.radians(pitch_deg))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --- Shared POC configuration ---


def test_sample_configuration_matches_the_demo_specification() -> None:
    poc = LONDON_BRIDGE_POC
    assert poc.source.name == "London Bridge, London, UK"
    assert (poc.source.latitude, poc.source.longitude) == (51.507921, -0.087700)
    assert poc.camera.camera_height_m == 1.5
    assert poc.camera.orientation == CameraOrientation(0.0, 20.0, 0.0)
    assert poc.camera.orientation_assumed is True
    assert poc.camera.profile is GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED
    assert poc.camera.profile.calibrated is False
    assert poc.camera.profile.diagonal_fov_deg == 85.0
    assert "ESTIMATED" in poc.camera.profile.name


def test_estimated_profile_uses_diagonal_fov_formula_at_image_size() -> None:
    expected_f = math.hypot(W, H) / (2 * math.tan(math.radians(85.0 / 2)))
    k = GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED.intrinsics_for(W, H)
    assert (k.fx, k.fy) == pytest.approx((expected_f, expected_f))
    assert (k.cx, k.cy, k.width, k.height) == (W / 2, H / 2, W, H)
    assert k.distortion == () and not k.has_distortion

    # Portrait images get the same diagonal focal length with swapped center.
    portrait = GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED.intrinsics_for(H, W)
    assert portrait.fx == pytest.approx(expected_f)
    assert (portrait.cx, portrait.cy) == (H / 2, W / 2)


def test_camera_setup_round_trips_through_provenance() -> None:
    setup = LONDON_BRIDGE_POC.camera
    assert CameraSetup.from_dict(setup.to_dict()) == setup

    calibrated = CameraSetup(
        profile=CalibratedProfile(name="measured", intrinsics=DISTORTED, reference="lab"),
        camera_height_m=1.2,
        orientation=CameraOrientation(45.0, 10.0, -2.0),
        orientation_assumed=False,
    )
    assert CameraSetup.from_dict(calibrated.to_dict()) == calibrated


@pytest.mark.parametrize(
    ("value", "enabled"),
    [("1", True), ("true", True), (" ON ", True), ("0", False), ("", False), ("off", False)],
)
def test_poc_flag_parsing(value: str, enabled: bool) -> None:
    config = configured_poc_geolocation({"INFRASCAN_POC_GEOLOCATION": value})
    assert (config is LONDON_BRIDGE_POC) is enabled


def test_poc_flag_absent_is_disabled_and_garbage_is_rejected() -> None:
    assert configured_poc_geolocation({}) is None
    with pytest.raises(RuntimeError, match="INFRASCAN_POC_GEOLOCATION"):
        configured_poc_geolocation({"INFRASCAN_POC_GEOLOCATION": "maybe"})


def test_bottom_center_contact_point() -> None:
    assert bbox_contact_point([100.0, 50.0, 300.0, 250.5]) == (200.0, 250.5)


# --- Known ground points ---


def test_principal_point_lands_at_height_over_tan_pitch() -> None:
    hit = ground(ESTIMATED.cx, ESTIMATED.cy)
    expected = HEIGHT_M / math.tan(math.radians(PITCH))
    assert hit.status is ProjectionStatus.ESTIMATED
    assert hit.east_m == pytest.approx(0.0, abs=1e-9)
    assert hit.north_m == pytest.approx(expected)
    assert hit.ground_distance_m == pytest.approx(expected)


def test_lower_and_right_pixels_are_nearer_and_east() -> None:
    center = ground(ESTIMATED.cx, ESTIMATED.cy)
    lower = ground(ESTIMATED.cx, ESTIMATED.cy + 100)
    right = ground(ESTIMATED.cx + 100, ESTIMATED.cy)
    assert lower.north_m < center.north_m
    assert right.east_m > 0
    assert right.north_m == pytest.approx(center.north_m)


@pytest.mark.parametrize(
    ("east", "north", "orientation"),
    [
        (0.0, 3.0, SAMPLE),
        (-1.2, 7.5, SAMPLE),
        (2.5, 12.0, SAMPLE),
        (4.0, 6.0, CameraOrientation(30.0, 15.0, 0.0)),
        (-3.0, -5.0, CameraOrientation(200.0, 25.0, 4.0)),
        (6.0, 1.0, CameraOrientation(80.0, 18.0, -6.0)),
    ],
)
def test_known_ground_points_round_trip(east, north, orientation) -> None:
    pixel = project_ground_to_pixel(east, north, ESTIMATED, orientation, HEIGHT_M)
    assert pixel is not None
    hit = ground(*pixel, orientation=orientation)
    assert hit.status is ProjectionStatus.ESTIMATED
    assert (hit.east_m, hit.north_m) == pytest.approx((east, north), abs=1e-6)


def test_known_ground_points_round_trip_through_lens_distortion() -> None:
    orientation = CameraOrientation(12.0, 22.0, 1.5)
    for east, north in [(0.0, 4.0), (-2.0, 6.0), (3.0, 9.0), (1.0, 2.5)]:
        pixel = project_ground_to_pixel(east, north, DISTORTED, orientation, HEIGHT_M)
        assert pixel is not None
        hit = project_pixel_to_ground(*pixel, DISTORTED, orientation, HEIGHT_M)
        assert hit.status is ProjectionStatus.ESTIMATED
        assert (hit.east_m, hit.north_m) == pytest.approx((east, north), abs=1e-6)

    # Ignoring the distortion would put the same pixel somewhere else.
    pixel = project_ground_to_pixel(3.0, 9.0, DISTORTED, orientation, HEIGHT_M)
    ideal = PinholeIntrinsics(**{**DISTORTED.as_dict(), "distortion": ()})
    naive = project_pixel_to_ground(*pixel, ideal, orientation, HEIGHT_M)
    assert abs(naive.east_m - 3.0) + abs(naive.north_m - 9.0) > 0.05


def test_undistortion_inverts_distortion() -> None:
    coefficients = DISTORTED.distortion
    for x, y in [(0.0, 0.0), (0.3, -0.2), (-0.5, 0.4), (0.6, 0.45)]:
        xd, yd = distort_normalized(x, y, coefficients)
        assert undistort_normalized(xd, yd, coefficients) == pytest.approx((x, y), abs=1e-10)


def test_divergent_undistortion_is_reported() -> None:
    k = PinholeIntrinsics(500, 500, 400, 300, 800, 600, distortion=(-5.0, 0, 0, 0, 0))
    hit = project_pixel_to_ground(800, 600, k, SAMPLE, HEIGHT_M)
    assert hit.status is ProjectionStatus.UNDISTORTION_FAILED
    assert hit.east_m is None


# --- Heading changes ---


@pytest.mark.parametrize("heading", [0.0, 37.0, 90.0, 180.0, 270.0, -90.0, 450.0])
def test_heading_rotates_the_ground_point_clockwise_from_north(heading: float) -> None:
    distance = HEIGHT_M / math.tan(math.radians(PITCH))
    hit = ground(ESTIMATED.cx, ESTIMATED.cy, orientation=CameraOrientation(heading, PITCH))
    h = math.radians(heading)
    assert hit.east_m == pytest.approx(distance * math.sin(h), abs=1e-9)
    assert hit.north_m == pytest.approx(distance * math.cos(h), abs=1e-9)
    assert hit.ground_distance_m == pytest.approx(distance)


def test_positive_roll_dips_the_right_side() -> None:
    # Rolling right side down makes the image's right half see nearer road.
    rolled = CameraOrientation(0.0, PITCH, 5.0)
    left = ground(ESTIMATED.cx - 200, ESTIMATED.cy, orientation=rolled)
    right = ground(ESTIMATED.cx + 200, ESTIMATED.cy, orientation=rolled)
    assert right.north_m < left.north_m


# --- Image scaling ---


@pytest.mark.parametrize("scale", [0.25, 0.5, 2.0, 4032 / 886])
def test_estimated_profile_is_invariant_to_uniform_image_scaling(scale: float) -> None:
    w, h = round(W * scale), round(H * scale)
    scaled = GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED.intrinsics_for(w, h)
    for u, v in [(443.0, 400.0), (600.0, 541.0), (120.0, 590.0)]:
        base = ground(u, v)
        other = ground(u * w / W, v * h / H, intrinsics=scaled)
        assert (other.east_m, other.north_m) == pytest.approx(
            (base.east_m, base.north_m), rel=2e-3
        )


def test_calibrated_intrinsics_scale_to_resized_images() -> None:
    orientation = CameraOrientation(0.0, 20.0, 0.0)
    half = DISTORTED.scaled_to(2000, 1500)
    assert (half.fx, half.fy, half.cx, half.cy) == (1550.0, 1545.0, 1005.0, 745.0)
    assert half.distortion == DISTORTED.distortion

    full = project_pixel_to_ground(2400, 2200, DISTORTED, orientation, HEIGHT_M)
    resized = project_pixel_to_ground(1200, 1100, half, orientation, HEIGHT_M)
    assert (resized.east_m, resized.north_m) == pytest.approx((full.east_m, full.north_m))


def test_cropped_or_rotated_images_cannot_reuse_calibration() -> None:
    with pytest.raises(IntrinsicsMismatchError):
        DISTORTED.scaled_to(3000, 3000)
    with pytest.raises(IntrinsicsMismatchError):
        DISTORTED.scaled_to(3000, 4000)

    setup = CameraSetup(
        profile=CalibratedProfile(name="measured", intrinsics=DISTORTED, reference="lab"),
        camera_height_m=HEIGHT_M,
        orientation=SAMPLE,
    )
    result = GroundPlaneGeolocator(setup, 51.5, -0.08, W, H).locate([400, 400, 500, 500])
    assert result.status is ProjectionStatus.INTRINSICS_MISMATCH
    assert result.location is None
    assert "aspect ratio" in result.details["message"]


# --- Invalid intersections ---


def test_pixels_on_and_above_the_horizon_never_reach_the_road() -> None:
    row = horizon_row(ESTIMATED, PITCH)
    for v in (row, row - 1, 0.0):
        hit = ground(ESTIMATED.cx, v)
        assert hit.status is ProjectionStatus.ABOVE_HORIZON
        assert hit.east_m is None and hit.north_m is None


def test_just_below_the_horizon_is_unstable_not_fabricated() -> None:
    row = horizon_row(ESTIMATED, PITCH)
    hit = ground(ESTIMATED.cx, row + 0.5)
    assert hit.status is ProjectionStatus.UNSTABLE_NEAR_HORIZON
    assert hit.east_m is None and hit.north_m is None
    assert hit.ground_distance_m > 50


def test_max_distance_limit_applies() -> None:
    # About 15 m ahead: accepted by default, rejected with a 10 m limit.
    pixel = project_ground_to_pixel(0.0, 15.0, ESTIMATED, SAMPLE, HEIGHT_M)
    assert ground(*pixel).status is ProjectionStatus.ESTIMATED
    strict = ground(*pixel, limits=ProjectionLimits(max_ground_distance_m=10.0))
    assert strict.status is ProjectionStatus.UNSTABLE_NEAR_HORIZON


def test_intersections_behind_the_camera_are_rejected() -> None:
    # A descending ray from a camera below the plane meets it behind the camera.
    assert intersect_ground_plane((0.0, 1.0, -0.5), -1.5) == (
        ProjectionStatus.BEHIND_CAMERA,
        None,
    )
    assert ground(ESTIMATED.cx, H - 1, camera_height_m=-1.5).status is (
        ProjectionStatus.BEHIND_CAMERA
    )
    # An upward ray from above the road also only meets it behind the camera.
    assert intersect_ground_plane((0.0, 1.0, 0.5), 1.5)[0] is ProjectionStatus.ABOVE_HORIZON
    assert project_ground_to_pixel(0.0, -5.0, ESTIMATED, SAMPLE, HEIGHT_M) is None


@pytest.mark.parametrize("u, v", [(math.nan, 300.0), (400.0, math.inf)])
def test_nonfinite_pixels_are_rejected(u: float, v: float) -> None:
    assert ground(u, v).status is ProjectionStatus.NONFINITE


def test_contact_points_outside_the_image_are_rejected() -> None:
    assert ground(ESTIMATED.cx, H + 5).status is ProjectionStatus.OUTSIDE_IMAGE
    assert ground(-3.0, H - 1).status is ProjectionStatus.OUTSIDE_IMAGE
    assert ground(W, H).status is ProjectionStatus.ESTIMATED


def test_camera_setup_rejects_nonpositive_height() -> None:
    with pytest.raises(ValueError):
        CameraSetup(profile=GALAXY_Z_FLIP7_MAIN_1X_ESTIMATED, camera_height_m=0.0, orientation=SAMPLE)


# --- WGS84 conversion ---


def test_offsets_convert_to_wgs84_with_ellipsoid_radii() -> None:
    a, e2 = 6_378_137.0, 0.0066943799901413165
    lat, lon = offset_to_wgs84(0.0, 0.0, 100.0, 100.0)
    assert lat == pytest.approx(math.degrees(100 / (a * (1 - e2))), rel=1e-12)
    assert lon == pytest.approx(math.degrees(100 / a), rel=1e-12)


@pytest.mark.parametrize(("east", "north"), [(0.0, 4.12), (3.6, 14.7), (-20.0, 35.0)])
def test_offsets_match_great_circle_distance_and_direction(east: float, north: float) -> None:
    src_lat, src_lon = LONDON_BRIDGE_POC.source.latitude, LONDON_BRIDGE_POC.source.longitude
    lat, lon = offset_to_wgs84(src_lat, src_lon, east, north)
    assert haversine_m(src_lat, src_lon, lat, lon) == pytest.approx(math.hypot(east, north), rel=5e-3)
    assert (lat > src_lat) == (north > 0)
    assert (lon > src_lon) == (east > 0)


def test_longitude_wraps_across_the_antimeridian() -> None:
    _, lon = offset_to_wgs84(0.0, 179.99999, 10.0, 0.0)
    assert -180 <= lon < -179.9999


def test_geolocator_produces_london_bridge_coordinates_for_sample_box() -> None:
    poc = LONDON_BRIDGE_POC
    geolocator = GroundPlaneGeolocator(
        poc.camera, poc.source.latitude, poc.source.longitude, W, H, basis="poc_assumed"
    )
    # Bottom-center at the principal point column, 100 px below center.
    result = geolocator.locate([400.0, 350.0, 486.0, H / 2 + 100])
    assert result.status is ProjectionStatus.ESTIMATED
    details = result.details
    assert details["basis"] == "poc_assumed"
    assert details["contact_pixel"] == [443.0, H / 2 + 100]
    assert details["east_m"] == pytest.approx(0.0, abs=1e-9)
    assert 1.5 < details["north_m"] < HEIGHT_M / math.tan(math.radians(PITCH))
    expected = offset_to_wgs84(poc.source.latitude, poc.source.longitude, 0.0, details["north_m"])
    assert (result.location["latitude"], result.location["longitude"]) == pytest.approx(expected)
