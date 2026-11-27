"""Turn decoded A119 Mini 2 record slots into a video-aligned GPS track.

Alignment policy (design choice awaiting real-hardware confirmation):

* The camera writes one record slot per recording second, starting at the
  beginning of the clip, so slot `k` sits roughly `k` seconds into the video.
* Only active fixes are emitted. Void / pre-lock records never anchor time.
* Consecutive active fixes keep their own timestamp spacing, which is not
  assumed to be exactly one second. A fix's video offset is
  `anchor + (fix time - first fix time)`, where `anchor` is the median of
  `slot - (fix time - first fix time)` over all kept fixes.
* A clock discontinuity (time running backwards, or a time step that differs
  from the slot step by more than `CLOCK_JUMP_SECONDS`) splits the fixes into
  epochs. The latest epoch is kept because the A119 Mini 2 corrects its clock
  from GPS; earlier fixes are discarded rather than guessed.
* Camera timestamps are local wall time stored as if they were UTC. The
  submitted camera offset converts them: UTC = camera time - offset.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from inference.telemetry.a119 import RawRecord, RecordSlot, TelemetryError

SLOT_SECONDS = 1.0
CLOCK_JUMP_SECONDS = 600.0
# Fixes this far outside the clip are not trusted.
DURATION_TOLERANCE_SECONDS = 2.0

MIN_UTC_OFFSET_MINUTES = -12 * 60
MAX_UTC_OFFSET_MINUTES = 14 * 60


class MissingGpsTrack(TelemetryError):
    """The clip has no usable active A119 GPS fix."""


@dataclass(frozen=True)
class GpsFix:
    frame_offset_seconds: float
    recorded_at: datetime
    latitude: float
    longitude: float
    speed_kmh: float | None
    course_deg: float | None


@dataclass(frozen=True)
class GpsTrack:
    fixes: tuple[GpsFix, ...]
    source: str
    record_count: int
    void_count: int
    invalid_count: int
    duplicate_count: int
    conflicting_duplicate_count: int
    discarded_epoch_count: int
    out_of_range_count: int
    cold_start_clock_jump_seconds: float | None

    @property
    def first_fix(self) -> GpsFix:
        return self.fixes[0]


def validate_camera_utc_offset(minutes: object) -> int:
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("camera_utc_offset_minutes must be an integer")
    if not MIN_UTC_OFFSET_MINUTES <= minutes <= MAX_UTC_OFFSET_MINUTES:
        raise ValueError("camera_utc_offset_minutes must be between -720 and 840")
    if minutes % 15:
        raise ValueError("camera_utc_offset_minutes must be a multiple of 15")
    return minutes


def _seconds(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds()


def build_track(
    slots: Sequence[RecordSlot],
    source: str,
    camera_utc_offset_minutes: int,
    duration_seconds: float | None = None,
) -> GpsTrack:
    offset = timedelta(minutes=validate_camera_utc_offset(camera_utc_offset_minutes))
    invalid_count = sum(slot.record is None for slot in slots)
    void_count = sum(
        slot.record is not None and not slot.record.active for slot in slots
    )

    kept: list[tuple[int, RawRecord]] = []
    epochs: list[list[tuple[int, RawRecord]]] = [kept]
    duplicate_count = 0
    conflicting_count = 0
    for index, slot in enumerate(slots):
        record = slot.record
        if record is None or not record.active:
            continue
        current = epochs[-1]
        if current:
            previous_index, previous = current[-1]
            step = _seconds(record.camera_time, previous.camera_time)
            if step == 0:
                # Stale repeats are byte-identical; a same-second record with
                # different values is ambiguous, so the first one wins.
                if record.payload == previous.payload:
                    duplicate_count += 1
                else:
                    conflicting_count += 1
                continue
            if step < 0 or abs(step - (index - previous_index) * SLOT_SECONDS) > CLOCK_JUMP_SECONDS:
                epochs.append([])
                current = epochs[-1]
        current.append((index, record))

    kept = epochs[-1]
    discarded_epoch_count = sum(len(epoch) for epoch in epochs[:-1])
    if not kept:
        raise MissingGpsTrack("The video has no active A119 GPS fix.")

    first_index, first = kept[0]
    cold_start_jump = None
    previous_void = next(
        (
            (index, slots[index].record)
            for index in range(first_index - 1, -1, -1)
            if slots[index].record is not None
            and not slots[index].record.active
            and slots[index].record.camera_time is not None
        ),
        None,
    )
    if previous_void is not None:
        void_index, void = previous_void
        jump = _seconds(first.camera_time, void.camera_time) - (first_index - void_index) * SLOT_SECONDS
        if abs(jump) >= CLOCK_JUMP_SECONDS:
            cold_start_jump = jump

    anchor = statistics.median(
        index * SLOT_SECONDS - _seconds(record.camera_time, first.camera_time)
        for index, record in kept
    )
    fixes = []
    out_of_range = 0
    for _, record in kept:
        frame_offset = anchor + _seconds(record.camera_time, first.camera_time)
        if frame_offset < -DURATION_TOLERANCE_SECONDS or (
            duration_seconds is not None
            and frame_offset > duration_seconds + DURATION_TOLERANCE_SECONDS
        ):
            out_of_range += 1
            continue
        fixes.append(
            GpsFix(
                frame_offset_seconds=frame_offset,
                recorded_at=record.camera_time.replace(tzinfo=UTC) - offset,
                latitude=record.latitude,
                longitude=record.longitude,
                speed_kmh=record.speed_kmh,
                course_deg=record.course_deg,
            )
        )
    if not fixes:
        raise MissingGpsTrack("No A119 GPS fix falls within the video duration.")
    return GpsTrack(
        fixes=tuple(fixes),
        source=source,
        record_count=len(slots),
        void_count=void_count,
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
        conflicting_duplicate_count=conflicting_count,
        discarded_epoch_count=discarded_epoch_count,
        out_of_range_count=out_of_range,
        cold_start_clock_jump_seconds=cold_start_jump,
    )
