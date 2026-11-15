"""Prototype geolocation, independent of YOLO and database dependencies."""

from inference.geolocation.camera_profiles import (
    CameraProfile,
    PROTOTYPE_HEADING_DEG,
    SAMPLE_CAMERA_PROFILE,
)
from inference.geolocation.projection import geolocate_detection

__all__ = [
    "CameraProfile", "PROTOTYPE_HEADING_DEG", "SAMPLE_CAMERA_PROFILE",
    "geolocate_detection",
]
