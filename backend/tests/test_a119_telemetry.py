"""A119 Mini 2 freeGPS decoding, track alignment, and frame matching.

The golden fixture `fixtures/a119_mini2_type3_golden.bin` was assembled by
hand with `xxd -r`, independently of both the parser and the seed generator;
its expected values below are literals, not recomputed with parser code.
"""

from __future__ import annotations

import io
import math
import os
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from inference.telemetry import (
    GpsFix,
    MissingGpsTrack,
    TelemetryError,
    build_track,
    locate_frame,
    read_a119_track,
    validate_camera_utc_offset,
)
from inference.telemetry.a119 import read_gps_index, read_record_slots, scan_markers
from inference.telemetry.matching import interpolate_heading

GOLDEN = Path(__file__).parent / "fixtures" / "a119_mini2_type3_golden.bin"
START = datetime(2026, 11, 20, 12, 30, 0)


def ddmm(value: float) -> float:
    degrees = int(abs(value))
    return degrees * 100 + (abs(value) - degrees) * 60


def block(
    when: datetime | None = START,
    *,
    status: bytes = b"A",
    latitude: float = 33.8031,
    longitude: float = -117.9146,
    knots: float = 21.6,
    course: float = 0.8,
    length: int = 0x2C,
    month: int | None = None,
    hemispheres: bytes | None = None,
    size: int = 0x40,
) -> bytes:
    when = when or START
    fields = struct.pack(
        "<7I", length, when.hour, when.minute, when.second, when.year - 2000,
        month if month is not None else when.month, when.day,
    )
    if status == b"A":
        refs = hemispheres or (b"N" if latitude >= 0 else b"S") + (b"E" if longitude >= 0 else b"W")
        values = struct.pack("<4f", ddmm(latitude), ddmm(longitude), knots, course)
    else:
        refs = hemispheres or b"00"
        values = bytes(16)
    data = struct.pack(">I", size) + b"freeGPS " + fields + status + refs + b"\0" + values
    return data + bytes(size - len(data))


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def container(blocks: list[bytes], *, index: list[tuple[int, int]] | None = None, filler: bytes = b"") -> bytes:
    """ftyp + mdat(blocks) + moov(gps index); `index=None` writes the true offsets."""
    ftyp = box(b"ftyp", b"isom\0\0\0\0isomavc1")
    mdat_payload = filler
    offsets = []
    for item in blocks:
        offsets.append(len(ftyp) + 8 + len(mdat_payload))
        mdat_payload += item + bytes(32)
    head = ftyp + box(b"mdat", mdat_payload)
    entries = index if index is not None else [(o, 0x40) for o in offsets]
    table = struct.pack(">II", 0, len(entries)) + b"".join(struct.pack(">II", *e) for e in entries)
    return head + box(b"moov", box(b"mvhd", bytes(100)) + box(b"gps ", table))


def slots_of(data: bytes, **kwargs):
    return read_record_slots(io.BytesIO(data), len(data), **kwargs)


def track_of(blocks: list[bytes], offset: int = -480, duration: float | None = None):
    slots, source = slots_of(container(blocks))
    return build_track(slots, source, offset, duration)


# --- Golden fixture ---


def test_golden_fixture_decodes_to_literal_values() -> None:
    data = GOLDEN.read_bytes()
    assert len(data) == 192
    slots, source = slots_of(data)
    assert source == "scan"
    assert [slot.file_offset for slot in slots] == [0, 64, 128]

    void, north_west, south_east = (slot.record for slot in slots)
    assert void.active is False
    assert void.camera_time == datetime(2026, 11, 20, 20, 29, 58)

    assert north_west.active is True
    assert north_west.camera_time == datetime(2026, 11, 20, 12, 30, 0)
    assert north_west.latitude == pytest.approx(33.803125, abs=1e-12)
    assert north_west.longitude == pytest.approx(-117.91458333333334, abs=1e-12)
    assert north_west.speed_kmh == pytest.approx(21.5 * 1.852, abs=1e-9)
    assert north_west.course_deg == 359.5

    assert south_east.latitude == pytest.approx(-33.875, abs=1e-12)
    assert south_east.longitude == pytest.approx(151.20416666666668, abs=1e-12)
    assert south_east.speed_kmh == 0.0
    assert south_east.course_deg == 0.25


def test_golden_fixture_track_uses_camera_offset_and_reports_cold_start() -> None:
    track = read_a119_track(GOLDEN, -480, 10.0)
    assert [fix.frame_offset_seconds for fix in track.fixes] == [1.0, 2.0]
    assert track.first_fix.recorded_at == datetime(2026, 11, 20, 20, 30, 0, tzinfo=UTC)
    assert track.fixes[1].recorded_at == datetime(2026, 11, 20, 20, 30, 1, tzinfo=UTC)
    assert (track.void_count, track.record_count) == (1, 3)
    # Pre-lock RTC (UTC 20:29:58) vs first fix (local 12:30:00, offset -8 h).
    assert track.cold_start_clock_jump_seconds == -28799.0


# --- Block discovery ---


def test_valid_index_is_preferred_over_stray_markers() -> None:
    decoy = b"\0\0\0\x40freeGPS " + bytes(56)  # inside media data, not indexed
    data = container([block(START), block(START + timedelta(seconds=1))], filler=decoy)
    slots, source = slots_of(data)
    assert source == "index"
    assert len(slots) == 2
    assert all(slot.record is not None for slot in slots)
    assert len(scan_markers(io.BytesIO(data), len(data))) == 3


@pytest.mark.parametrize(
    "entries",
    [
        [(10_000_000, 0x40)],  # past EOF
        [(0, 0x40)],  # does not point at a marker
        [(60, 4)],  # size below one record
    ],
)
def test_unusable_index_falls_back_to_marker_scan(entries) -> None:
    blocks = [block(START), block(START + timedelta(seconds=1))]
    data = container(blocks, index=entries)
    assert read_gps_index(io.BytesIO(data), len(data)) is None
    slots, source = slots_of(data)
    assert source == "scan"
    assert len(slots) == 2


def test_index_count_larger_than_box_is_rejected() -> None:
    data = container([block(START)])
    corrupted = data.replace(struct.pack(">II", 0, 1), struct.pack(">II", 0, 500))
    assert read_gps_index(io.BytesIO(corrupted), len(corrupted)) is None


def test_missing_index_uses_scan() -> None:
    data = container([block(START)], index=[])
    assert slots_of(data)[1] == "scan"


@pytest.mark.parametrize("chunk_bytes", [8, 9, 11, 13, 17, 64, 1 << 20])
def test_scan_finds_markers_split_across_reads_exactly_once(chunk_bytes: int) -> None:
    pieces = [b"\x01" * 3, block(START), b"\x02" * 7, block(START + timedelta(seconds=1)), b"\x03"]
    data = b"".join(pieces)
    expected = [3, 3 + 0x40 + 7]
    assert scan_markers(io.BytesIO(data), len(data), chunk_bytes=chunk_bytes) == expected


def test_scan_record_limit_fails_closed() -> None:
    data = block(START) * 4
    with pytest.raises(TelemetryError):
        scan_markers(io.BytesIO(data), len(data), max_records=3)


def test_truncated_block_at_end_of_file_is_invalid() -> None:
    data = block(START) + block(START + timedelta(seconds=1))[:40]
    slots, _ = slots_of(data)
    assert slots[1].record is None
    assert slots[1].invalid_reason == "truncated"


# --- Record validation ---


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"month": 13}, "invalid_datetime"),
        ({"status": b"X"}, "unknown_status"),
        ({"hemispheres": b"0W"}, "invalid_hemisphere"),
        ({"length": 0x3F0}, "unsupported_layout"),
        ({"latitude": 0.0, "longitude": 0.0, "hemispheres": b"NE"}, "null_island"),
    ],
)
def test_corrupt_records_are_rejected_not_guessed(kwargs: dict, reason: str) -> None:
    slots, _ = slots_of(block(**kwargs))
    assert slots[0].record is None
    assert slots[0].invalid_reason == reason


def test_minutes_of_sixty_or_more_are_out_of_range() -> None:
    raw = bytearray(block())
    struct.pack_into("<f", raw, 0x2C, 3360.5)  # 33 degrees 60.5 minutes
    slots, _ = slots_of(bytes(raw))
    assert slots[0].invalid_reason == "coordinate_out_of_range"


def test_out_of_range_speed_and_course_are_dropped_not_fatal() -> None:
    raw = bytearray(block())
    struct.pack_into("<f", raw, 0x34, float("nan"))
    struct.pack_into("<f", raw, 0x38, 400.0)
    record = slots_of(bytes(raw))[0][0].record
    assert record is not None
    assert (record.speed_kmh, record.course_deg) == (None, None)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(33.8, -117.9), (-33.87, 151.2), (51.5, 0.12), (-22.9, -43.2)],
)
def test_hemisphere_signs(latitude: float, longitude: float) -> None:
    record = slots_of(block(latitude=latitude, longitude=longitude))[0][0].record
    assert record.latitude == pytest.approx(latitude, abs=1e-4)
    assert record.longitude == pytest.approx(longitude, abs=1e-4)


# --- Track building ---


def test_void_records_are_counted_but_never_emitted() -> None:
    blocks = [block(START, status=b"V")] + [
        block(START + timedelta(seconds=s)) for s in range(1, 4)
    ]
    track = track_of(blocks)
    assert track.void_count == 1
    assert [fix.frame_offset_seconds for fix in track.fixes] == [1.0, 2.0, 3.0]


def test_all_void_track_is_missing_gps() -> None:
    with pytest.raises(MissingGpsTrack):
        track_of([block(START + timedelta(seconds=s), status=b"V") for s in range(3)])


def test_file_without_records_is_missing_gps(tmp_path: Path) -> None:
    path = tmp_path / "empty.mp4"
    path.write_bytes(box(b"ftyp", b"isom\0\0\0\0") + box(b"mdat", bytes(256)))
    with pytest.raises(MissingGpsTrack):
        read_a119_track(path, 0)


def test_stale_repeats_and_conflicting_same_second_records() -> None:
    first = block(START)
    conflicting = block(START + timedelta(seconds=1), latitude=34.5)
    blocks = [first, first, block(START + timedelta(seconds=1)), conflicting]
    track = track_of(blocks)
    assert track.duplicate_count == 1
    assert track.conflicting_duplicate_count == 1
    assert len(track.fixes) == 2
    assert track.fixes[1].latitude == pytest.approx(33.8031, abs=1e-4)


def test_cold_start_jump_keeps_slot_alignment() -> None:
    # Pre-lock RTC is UTC; post-lock camera time is local (UTC-8).
    blocks = [
        block(START + timedelta(hours=8, seconds=s), status=b"V") for s in range(3)
    ] + [block(START + timedelta(seconds=s)) for s in range(3, 6)]
    track = track_of(blocks, -480, 6.0)
    assert track.cold_start_clock_jump_seconds == -28800.0
    assert [fix.frame_offset_seconds for fix in track.fixes] == [3.0, 4.0, 5.0]
    assert track.first_fix.recorded_at == datetime(2026, 11, 20, 20, 30, 3, tzinfo=UTC)


def test_clock_discontinuity_keeps_only_the_latest_epoch() -> None:
    blocks = [block(START + timedelta(seconds=s)) for s in range(2)] + [
        block(START - timedelta(hours=1) + timedelta(seconds=s)) for s in range(2, 5)
    ]
    track = track_of(blocks)
    assert track.discarded_epoch_count == 2
    assert [fix.frame_offset_seconds for fix in track.fixes] == [2.0, 3.0, 4.0]


def test_fixes_far_outside_the_video_are_dropped() -> None:
    track = track_of([block(START + timedelta(seconds=s)) for s in range(10)], duration=4.0)
    assert track.out_of_range_count == 3
    assert track.fixes[-1].frame_offset_seconds == 6.0


@pytest.mark.parametrize("offset", [-720, -480, 0, 330, 345, 840])
def test_valid_camera_offsets(offset: int) -> None:
    assert validate_camera_utc_offset(offset) == offset


@pytest.mark.parametrize("offset", [-735, 855, 10, 1.5, "60", True, None])
def test_invalid_camera_offsets(offset) -> None:
    with pytest.raises(ValueError):
        validate_camera_utc_offset(offset)


# --- Frame matching ---


def fix(t: float, lat: float, lon: float, course: float | None = 0.0) -> GpsFix:
    return GpsFix(t, datetime(2026, 11, 20, tzinfo=UTC), lat, lon, 40.0, course)


def test_exact_and_interpolated_frames() -> None:
    fixes = [fix(1.0, 33.0, -117.0, 10.0), fix(2.0, 33.001, -117.002, 20.0)]
    exact = locate_frame(fixes, 1.0)
    assert (exact.method, exact.latitude, exact.heading_deg) == ("exact", 33.0, 10.0)
    middle = locate_frame(fixes, 1.25)
    assert middle.method == "interpolated"
    assert middle.latitude == pytest.approx(33.00025)
    assert middle.longitude == pytest.approx(-117.0005)
    assert middle.heading_deg == pytest.approx(12.5)


def test_heading_interpolates_across_north() -> None:
    assert interpolate_heading(350.0, 10.0, 0.5) == pytest.approx(0.0, abs=1e-9)
    assert interpolate_heading(10.0, 350.0, 0.25) == pytest.approx(5.0)
    fixes = [fix(0.0, 33.0, -117.0, 356.0), fix(1.0, 33.0001, -117.0, 4.0)]
    assert locate_frame(fixes, 0.75).heading_deg == pytest.approx(2.0)


def test_zero_heading_is_kept_not_treated_as_missing() -> None:
    fixes = [fix(0.0, 33.0, -117.0, 0.0), fix(1.0, 33.0, -116.999, 90.0)]
    assert locate_frame(fixes, 0.0).heading_deg == 0.0


def test_missing_course_derives_heading_from_motion() -> None:
    fixes = [fix(0.0, 33.0, -117.0, None), fix(1.0, 33.0001, -117.0, None)]
    assert locate_frame(fixes, 0.5).heading_deg == pytest.approx(0.0, abs=1e-6)
    east = [fix(0.0, 0.0, 10.0, None), fix(1.0, 0.0, 10.0001, None)]
    assert locate_frame(east, 0.0).heading_deg == pytest.approx(90.0, abs=1e-6)


def test_stationary_vehicle_without_course_has_no_heading() -> None:
    fixes = [fix(0.0, 33.0, -117.0, None), fix(1.0, 33.0, -117.0, None)]
    location = locate_frame(fixes, 0.5)
    assert location is not None and location.heading_deg is None


def test_long_gaps_are_not_bridged() -> None:
    fixes = [fix(0.0, 33.0, -117.0), fix(10.0, 33.01, -117.0)]
    assert locate_frame(fixes, 5.0) is None
    near = locate_frame(fixes, 0.9)
    assert near.method == "nearest" and near.latitude == 33.0
    assert locate_frame(fixes, 1.1) is None


def test_frames_outside_track_use_only_close_fixes() -> None:
    fixes = [fix(3.0, 33.0, -117.0), fix(4.0, 33.0001, -117.0)]
    assert locate_frame(fixes, 2.0).method == "nearest"
    assert locate_frame(fixes, 1.9) is None
    assert locate_frame(fixes, 5.0).method == "nearest"
    assert locate_frame([], 1.0) is None
    assert locate_frame(fixes, math.nan) is None


def test_longitude_interpolates_across_the_antimeridian() -> None:
    fixes = [fix(0.0, -17.0, 179.9999), fix(1.0, -17.0, -179.9999)]
    assert abs(locate_frame(fixes, 0.5).longitude) == pytest.approx(180.0, abs=1e-6)


# --- Optional real-camera fixture (never committed) ---


@pytest.mark.skipif(
    not os.environ.get("INFRASCAN_A119_REAL_CLIP"),
    reason="set INFRASCAN_A119_REAL_CLIP to a local A119 Mini 2 MP4 to run",
)
def test_optional_real_clip_parses() -> None:
    path = Path(os.environ["INFRASCAN_A119_REAL_CLIP"])
    offset = int(os.environ.get("INFRASCAN_A119_REAL_CLIP_OFFSET_MINUTES", "0"))
    track = read_a119_track(path, offset)
    assert track.fixes
    assert all(-90 <= f.latitude <= 90 and -180 <= f.longitude <= 180 for f in track.fixes)
