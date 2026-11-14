"""Per-detection geolocation by inverse projection onto a flat road plane."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.geolocation.camera import CameraSetup
from app.geolocation.projection import (
    IntrinsicsMismatchError,
    PinholeIntrinsics,
    ProjectionStatus,
    bbox_contact_point,
    offset_to_wgs84,
    project_pixel_to_ground,
)

METHOD = "inverse_projection_flat_ground"


@dataclass(frozen=True)
class DetectionGeolocation:
    status: ProjectionStatus
    location: dict[str, float] | None
    details: dict[str, Any]


class GroundPlaneGeolocator:
    """Geolocates detections from one image relative to its source GPS.

    `image_width`/`image_height` are the oriented dimensions of the image the
    detector saw; bounding boxes must use the same pixel grid.
    """

    def __init__(
        self,
        setup: CameraSetup,
        source_latitude: float,
        source_longitude: float,
        image_width: int,
        image_height: int,
        basis: str | None = None,
    ) -> None:
        self._setup = setup
        self._source = (source_latitude, source_longitude)
        self._basis = basis
        self.intrinsics: PinholeIntrinsics | None
        self.intrinsics_error: str | None = None
        try:
            self.intrinsics = setup.profile.intrinsics_for(image_width, image_height)
        except IntrinsicsMismatchError as exc:
            self.intrinsics = None
            self.intrinsics_error = str(exc)

    def _details(self, contact: tuple[float, float], **values: Any) -> dict[str, Any]:
        details: dict[str, Any] = {"method": METHOD, "contact_pixel": list(contact)}
        if self._basis is not None:
            details["basis"] = self._basis
        details.update({k: v for k, v in values.items() if v is not None})
        return details

    def locate(self, bbox: Sequence[float]) -> DetectionGeolocation:
        contact = bbox_contact_point(bbox)
        if self.intrinsics is None:
            return DetectionGeolocation(
                ProjectionStatus.INTRINSICS_MISMATCH,
                None,
                self._details(contact, message=self.intrinsics_error),
            )

        hit = project_pixel_to_ground(
            *contact,
            intrinsics=self.intrinsics,
            orientation=self._setup.orientation,
            camera_height_m=self._setup.camera_height_m,
            limits=self._setup.limits,
        )
        if hit.status is not ProjectionStatus.ESTIMATED:
            return DetectionGeolocation(
                hit.status,
                None,
                self._details(
                    contact, ground_distance_m=hit.ground_distance_m, message=hit.message
                ),
            )

        assert hit.east_m is not None and hit.north_m is not None
        try:
            latitude, longitude = offset_to_wgs84(*self._source, hit.east_m, hit.north_m)
        except ValueError as exc:
            return DetectionGeolocation(
                ProjectionStatus.NONFINITE, None, self._details(contact, message=str(exc))
            )
        return DetectionGeolocation(
            ProjectionStatus.ESTIMATED,
            {"latitude": latitude, "longitude": longitude},
            self._details(
                contact,
                east_m=hit.east_m,
                north_m=hit.north_m,
                ground_distance_m=hit.ground_distance_m,
            ),
        )
