"""Map a sampled frame's video timestamp to a trustworthy GPS position.

Policy:

* Between two bracketing fixes at most `MAX_INTERPOLATION_GAP_SECONDS` apart,
  latitude/longitude are interpolated linearly and heading circularly.
* Otherwise the closest fix is used only if it is within
  `NEAREST_FIX_TOLERANCE_SECONDS` of the frame.
* Longer outages (tunnels, lost lock) are never bridged; such frames get no
  location.
* When a fix has no course, heading is derived from the bracketing (or
  neighboring) fixes if they are at least `MIN_HEADING_BASELINE_M` apart.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass

from inference.telemetry.track import GpsFix

MAX_INTERPOLATION_GAP_SECONDS = 5.0
NEAREST_FIX_TOLERANCE_SECONDS = 1.0
MIN_HEADING_BASELINE_M = 3.0
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class FrameLocation:
    latitude: float
    longitude: float
    heading_deg: float | None
    method: str


def _wrap_delta(start: float, end: float) -> float:
    """Signed shortest angular step from `start` to `end`, in (-180, 180]."""
    delta = (end - start) % 360
    return delta - 360 if delta > 180 else delta


def interpolate_heading(start: float, end: float, fraction: float) -> float:
    return (start + fraction * _wrap_delta(start, end)) % 360


def distance_m(a: GpsFix, b: GpsFix) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(_wrap_delta(a.longitude, b.longitude))
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: GpsFix, b: GpsFix) -> float:
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlon = math.radians(_wrap_delta(a.longitude, b.longitude))
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360


def _derived_heading(a: GpsFix | None, b: GpsFix | None) -> float | None:
    if a is None or b is None:
        return None
    if b.frame_offset_seconds - a.frame_offset_seconds > MAX_INTERPOLATION_GAP_SECONDS:
        return None
    if distance_m(a, b) < MIN_HEADING_BASELINE_M:
        return None
    return bearing_deg(a, b)


def _fix_heading(fixes: Sequence[GpsFix], index: int) -> float | None:
    fix = fixes[index]
    if fix.course_deg is not None:
        return fix.course_deg
    previous = fixes[index - 1] if index > 0 else None
    following = fixes[index + 1] if index + 1 < len(fixes) else None
    heading = _derived_heading(previous, fix)
    return heading if heading is not None else _derived_heading(fix, following)


def locate_frame(fixes: Sequence[GpsFix], frame_seconds: float) -> FrameLocation | None:
    """`fixes` must be sorted by `frame_offset_seconds`."""
    if not fixes or not math.isfinite(frame_seconds):
        return None
    offsets = [fix.frame_offset_seconds for fix in fixes]
    right = bisect.bisect_left(offsets, frame_seconds)
    after = fixes[right] if right < len(fixes) else None
    before = fixes[right - 1] if right > 0 else None
    if after is not None and after.frame_offset_seconds == frame_seconds:
        return FrameLocation(after.latitude, after.longitude, _fix_heading(fixes, right), "exact")

    if before is not None and after is not None:
        gap = after.frame_offset_seconds - before.frame_offset_seconds
        if gap <= MAX_INTERPOLATION_GAP_SECONDS:
            fraction = (frame_seconds - before.frame_offset_seconds) / gap
            latitude = before.latitude + fraction * (after.latitude - before.latitude)
            longitude = before.longitude + fraction * _wrap_delta(before.longitude, after.longitude)
            longitude = (longitude + 180) % 360 - 180
            if before.course_deg is not None and after.course_deg is not None:
                heading = interpolate_heading(before.course_deg, after.course_deg, fraction)
            else:
                heading = _derived_heading(before, after)
            return FrameLocation(latitude, longitude, heading, "interpolated")

    candidates = [index for index in (right - 1, right) if 0 <= index < len(fixes)]
    nearest = min(
        candidates, key=lambda index: abs(fixes[index].frame_offset_seconds - frame_seconds)
    )
    fix = fixes[nearest]
    if abs(fix.frame_offset_seconds - frame_seconds) > NEAREST_FIX_TOLERANCE_SECONDS:
        return None
    return FrameLocation(fix.latitude, fix.longitude, _fix_heading(fixes, nearest), "nearest")
