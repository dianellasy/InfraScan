"""Analytical geometry checks; no model weights, database or GPS device needed."""

from dataclasses import replace
import math

import pytest

from inference.geolocation import CameraProfile, a119_mini2_1080p_profile, geolocate_detection
from inference.geolocation.projection import (
    EARTH_RADIUS_M, GroundOffset, offset_to_gps, project_to_ground,
)

CAMERA = CameraProfile(
    name="analytical-test", image_width=1000, image_height=800,
    fx_px=500, fy_px=500, cx_px=500, cy_px=400, height_m=2,
)
BOX = [700, 500, 800, 600]


def test_level_projection_matches_known_ground_point() -> None:
    # Bottom center (750,600): x/z=0.5, height/z=0.4 -> z=5m, x=2.5m.
    assert project_to_ground(BOX, 1000, 800, CAMERA) == GroundOffset(2.5, 5.0)


@pytest.mark.parametrize("sx,sy", [(0.5, 0.5), (2, 2), (0.5, 1)])
def test_resizing_full_frame_preserves_ground_offset(sx: float, sy: float) -> None:
    box = [BOX[0] * sx, BOX[1] * sy, BOX[2] * sx, BOX[3] * sy]
    assert project_to_ground(box, int(1000 * sx), int(800 * sy), CAMERA) == GroundOffset(2.5, 5.0)


def test_a119_profile_projects_1440p_like_the_same_1080p_box() -> None:
    profile = a119_mini2_1080p_profile({})
    box_1080p = [900, 700, 1100, 900]
    expected = project_to_ground(box_1080p, 1920, 1080, profile)
    assert expected is not None
    offset = project_to_ground([v * 4 / 3 for v in box_1080p], 2560, 1440, profile)
    assert offset.right_m == pytest.approx(expected.right_m)
    assert offset.forward_m == pytest.approx(expected.forward_m)


def test_downward_pitch_intersects_road_at_image_center() -> None:
    # A 45-degree downward center ray meets the ground 2m ahead of a 2m camera.
    offset = project_to_ground([450, 350, 550, 400], 1000, 800, replace(CAMERA, pitch_down_deg=45))
    assert offset.right_m == 0
    assert offset.forward_m == pytest.approx(2)


@pytest.mark.parametrize("box", [
    [450, 350, 550, 400],  # on horizon
    [450, 300, 550, 350],  # above horizon
    [450, 350, 550, 401],  # near horizon, beyond 100m limit
    [-1, 500, 100, 600], [900, 500, 1001, 600], [700, 500, 800, 801],
    [800, 500, 700, 600], [700, 600, 800, 500], [700, 500, 700, 600],
    [700, 500, float("nan"), 600], [700, 500, 800, float("inf")], [1, 2, 3],
])
def test_unprojectable_detection_keeps_null_location(box: list[float]) -> None:
    assert geolocate_detection(
        box, 1000, 800, latitude=33, longitude=-117, heading_deg=0, profile=CAMERA,
    ) is None


@pytest.mark.parametrize("heading,latitude_sign,longitude_sign", [
    (0, 1, 1), (90, -1, 1), (180, -1, -1), (270, 1, -1), (450, -1, 1),
])
def test_heading_rotates_forward_and_right(heading: float, latitude_sign: int, longitude_sign: int) -> None:
    location = offset_to_gps(GroundOffset(2.5, 5), 0, 0, heading)
    assert math.copysign(1, location["latitude"]) == latitude_sign
    assert math.copysign(1, location["longitude"]) == longitude_sign


def test_northward_distance_has_expected_latitude_change() -> None:
    location = offset_to_gps(GroundOffset(0, 10), 33, -117, 0)
    assert location["latitude"] == pytest.approx(33 + math.degrees(10 / EARTH_RADIUS_M))
    assert location["longitude"] == pytest.approx(-117)


def test_eastward_distance_wraps_antimeridian() -> None:
    location = offset_to_gps(GroundOffset(0, 100), 0, 179.9999, 90)
    assert location["latitude"] == pytest.approx(0)
    assert location["longitude"] == pytest.approx(179.9999 + math.degrees(100 / EARTH_RADIUS_M) - 360)


def test_offsets_are_finite_near_poles_and_zero_offset_preserves_source() -> None:
    assert offset_to_gps(GroundOffset(0, 0), 12, 34, 0) == {"latitude": 12, "longitude": 34}
    location = offset_to_gps(GroundOffset(10, 10), 89.999999, 30, 45)
    assert -90 <= location["latitude"] <= 90
    assert -180 <= location["longitude"] <= 180


@pytest.mark.parametrize("changes", [
    {"fx_px": 0}, {"fy_px": -1}, {"height_m": 0}, {"image_width": 0},
    {"image_height": True}, {"cx_px": -1}, {"cy_px": 900},
    {"pitch_down_deg": 90}, {"fx_px": float("nan")}, {"max_ground_distance_m": -1},
])
def test_invalid_camera_settings_are_rejected(changes: dict) -> None:
    with pytest.raises(ValueError):
        replace(CAMERA, **changes)


@pytest.mark.parametrize("width,height", [(0, 800), (1000, -1), (True, 800), (1000.0, 800)])
def test_invalid_prediction_dimensions_are_rejected(width, height) -> None:
    with pytest.raises(ValueError):
        project_to_ground(BOX, width, height, CAMERA)


@pytest.mark.parametrize("latitude,longitude,heading", [(91, 0, 0), (0, -181, 0), (0, 0, float("nan"))])
def test_invalid_capture_metadata_is_rejected(latitude, longitude, heading) -> None:
    with pytest.raises(ValueError):
        offset_to_gps(GroundOffset(0, 5), latitude, longitude, heading)
