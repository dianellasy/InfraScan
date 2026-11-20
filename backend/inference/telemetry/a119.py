"""VIOFO A119 Mini 2 (Novatek) `freeGPS ` record decoding.

The A119 Mini 2 embeds one proprietary GPS record per recording second. Each
record is a box whose 4-byte big-endian size is followed by the bytes
`freeGPS ` (a `free` box whose payload starts with `GPS `). Offsets below are
relative to that size dword, the convention used by ExifTool and by the
`moov/gps ` index, so the marker itself starts at offset 4.

Confirmed layout (all multi-byte fields little-endian unless noted):

    0x00  u32 BE  box size (not trusted when found by the marker scan)
    0x04  8 B     b"freeGPS "
    0x0C  u32     record length; 0x2C on the A119 Mini 2
    0x10  6 x u32 hour, minute, second, year - 2000, month, day (camera clock)
    0x28  1 B     fix status: b"A" active, b"V" void / no satellite lock
    0x29  1 B     latitude hemisphere b"N" / b"S" (b"0" before the first fix)
    0x2A  1 B     longitude hemisphere b"E" / b"W" (b"0" before the first fix)
    0x2B  1 B     padding
    0x2C  f32     latitude, NMEA DDMM.MMMM
    0x30  f32     longitude, NMEA DDDMM.MMMM
    0x34  f32     speed over ground, knots
    0x38  f32     course over ground, degrees clockwise from true north

Sources that agree on these offsets (see README "A119 Mini 2 GPS metadata"):
ExifTool 12.76 QuickTimeStream.pl GPSType "1C"; ibragari/route_overlay
lib/novatek_gps.rb at 66f8448a; everydashcam freegps.ts LAYOUT_ALT at
21af3a04. Other Novatek layouts (for example the datetime-at-0x30 "Type 3"
variant) are deliberately rejected instead of guessed.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

MARKER = b"freeGPS "
MARKER_OFFSET = 4
RECORD_LENGTH_OFFSET = 0x0C
A119_RECORD_LENGTH = 0x2C
DATETIME_OFFSET = 0x10
STATUS_OFFSET = 0x28
LATITUDE_REF_OFFSET = 0x29
LONGITUDE_REF_OFFSET = 0x2A
VALUES_OFFSET = 0x2C
RECORD_BYTES = 0x3C

MAX_BLOCK_BYTES = 1 << 20
# One record per second for at most 600 s, with generous slack for stale
# duplicates. Anything larger is not an A119 clip this service accepts.
MAX_RECORDS = 2_400
SCAN_CHUNK_BYTES = 1 << 20
MAX_BOXES = 4_096

KNOTS_TO_KMH = 1.852
MAX_SPEED_KNOTS = 300.0
MIN_YEAR = 2010

_DATETIME = struct.Struct("<6I")
_VALUES = struct.Struct("<4f")
_U32_BE = struct.Struct(">I")
_U64_BE = struct.Struct(">Q")


class TelemetryError(ValueError):
    """The file's GPS metadata cannot be read within the supported contract."""


class InvalidRecord(ValueError):
    """One `freeGPS ` block does not hold a usable A119 Mini 2 record."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RawRecord:
    """One decoded record on the camera's own clock (timezone-naive)."""

    file_offset: int
    camera_time: datetime | None
    active: bool
    latitude: float | None = None
    longitude: float | None = None
    speed_kmh: float | None = None
    course_deg: float | None = None
    # Bytes 0x0C..0x3C, used to recognize byte-identical stale repeats.
    payload: bytes = b""


@dataclass(frozen=True)
class RecordSlot:
    """A `freeGPS ` block position in recording order and its decode result."""

    file_offset: int
    record: RawRecord | None
    invalid_reason: str | None = None


def nmea_to_degrees(value: float, max_degrees: int) -> float:
    """Convert unsigned NMEA DDMM.MMMM / DDDMM.MMMM to decimal degrees."""
    if not math.isfinite(value) or value < 0:
        raise InvalidRecord("coordinate_out_of_range")
    degrees = int(value // 100)
    minutes = value - degrees * 100
    if minutes >= 60 or degrees > max_degrees:
        raise InvalidRecord("coordinate_out_of_range")
    decimal = degrees + minutes / 60
    if decimal > max_degrees:
        raise InvalidRecord("coordinate_out_of_range")
    return decimal


def _camera_time(block: bytes) -> datetime | None:
    hour, minute, second, year, month, day = _DATETIME.unpack_from(block, DATETIME_OFFSET)
    if year > 99 or 2000 + year < MIN_YEAR:
        return None
    try:
        return datetime(2000 + year, month, day, hour, minute, second)
    except ValueError:
        return None


def decode_record(block: bytes, file_offset: int = 0) -> RawRecord:
    """Decode the first `RECORD_BYTES` of a block that starts at its size dword."""
    if len(block) < RECORD_BYTES:
        raise InvalidRecord("truncated")
    if block[MARKER_OFFSET:MARKER_OFFSET + len(MARKER)] != MARKER:
        raise InvalidRecord("missing_marker")
    (length,) = struct.unpack_from("<I", block, RECORD_LENGTH_OFFSET)
    if length != A119_RECORD_LENGTH:
        raise InvalidRecord("unsupported_layout")

    status = block[STATUS_OFFSET:STATUS_OFFSET + 1]
    camera_time = _camera_time(block)
    payload = bytes(block[RECORD_LENGTH_OFFSET:RECORD_BYTES])
    if status == b"V":
        return RawRecord(file_offset, camera_time, active=False, payload=payload)
    if status != b"A":
        raise InvalidRecord("unknown_status")
    if camera_time is None:
        raise InvalidRecord("invalid_datetime")

    latitude_ref = block[LATITUDE_REF_OFFSET:LATITUDE_REF_OFFSET + 1]
    longitude_ref = block[LONGITUDE_REF_OFFSET:LONGITUDE_REF_OFFSET + 1]
    if latitude_ref not in (b"N", b"S") or longitude_ref not in (b"E", b"W"):
        raise InvalidRecord("invalid_hemisphere")

    raw_lat, raw_lon, knots, course = _VALUES.unpack_from(block, VALUES_OFFSET)
    latitude = nmea_to_degrees(raw_lat, 90)
    longitude = nmea_to_degrees(raw_lon, 180)
    if latitude == 0 and longitude == 0:
        raise InvalidRecord("null_island")
    if latitude_ref == b"S":
        latitude = -latitude
    if longitude_ref == b"W":
        longitude = -longitude

    speed_kmh = (
        knots * KNOTS_TO_KMH
        if math.isfinite(knots) and 0 <= knots <= MAX_SPEED_KNOTS
        else None
    )
    course_deg = course % 360 if math.isfinite(course) and 0 <= course <= 360 else None
    return RawRecord(
        file_offset, camera_time, True, latitude, longitude, speed_kmh, course_deg, payload
    )


def _read_exact(f: BinaryIO, offset: int, size: int) -> bytes:
    f.seek(offset)
    return f.read(size)


def _iter_boxes(f: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int, int]]:
    """Yield (type, box start, payload start, box end); stop on malformed sizes."""
    position = start
    for _ in range(MAX_BOXES):
        if position + 8 > end:
            return
        header = _read_exact(f, position, 8)
        if len(header) < 8:
            return
        (size,) = _U32_BE.unpack_from(header)
        box_type = header[4:8]
        payload = position + 8
        if size == 1:
            large = _read_exact(f, position + 8, 8)
            if len(large) < 8:
                return
            (size,) = _U64_BE.unpack(large)
            payload = position + 16
        elif size == 0:
            size = end - position
        if size < payload - position or position + size > end:
            return
        yield box_type, position, payload, position + size
        position += size


def read_gps_index(f: BinaryIO, file_size: int) -> list[int] | None:
    """Block offsets from a structurally valid `moov/gps ` index, else None.

    The index payload is a 4-byte header, a big-endian entry count, then
    big-endian (absolute offset, size) pairs. Any out-of-bounds entry or entry
    that does not point at a `freeGPS ` marker makes the whole index unusable.
    """
    for box_type, _, moov_payload, moov_end in _iter_boxes(f, 0, file_size):
        if box_type != b"moov":
            continue
        for child_type, _, payload, end in _iter_boxes(f, moov_payload, moov_end):
            if child_type != b"gps ":
                continue
            available = end - payload
            if available < 8:
                return None
            header = _read_exact(f, payload, 8)
            (count,) = _U32_BE.unpack_from(header, 4)
            if count == 0 or count > MAX_RECORDS or 8 + count * 8 > available:
                return None
            table = _read_exact(f, payload + 8, count * 8)
            if len(table) != count * 8:
                return None
            offsets = []
            for index in range(count):
                offset, size = struct.unpack_from(">II", table, index * 8)
                if offset == 0 and size == 0:
                    continue
                if not (RECORD_BYTES <= size <= MAX_BLOCK_BYTES) or offset + size > file_size:
                    return None
                marker = _read_exact(f, offset + MARKER_OFFSET, len(MARKER))
                if marker != MARKER:
                    return None
                offsets.append(offset)
            return offsets or None
        return None
    return None


def scan_markers(
    f: BinaryIO,
    file_size: int,
    *,
    chunk_bytes: int = SCAN_CHUNK_BYTES,
    max_records: int = MAX_RECORDS,
) -> list[int]:
    """Find block offsets by a chunked marker scan that never loads the file.

    Each read is at most `chunk_bytes`; a `len(MARKER) - 1` byte tail is kept
    so a marker split across two reads is still found exactly once.
    """
    if chunk_bytes < len(MARKER):
        raise ValueError("chunk_bytes must be at least the marker length")
    overlap = len(MARKER) - 1
    offsets: list[int] = []
    tail = b""
    position = 0
    f.seek(0)
    while position < file_size:
        chunk = f.read(min(chunk_bytes, file_size - position))
        if not chunk:
            break
        window = tail + chunk
        base = position - len(tail)
        found = window.find(MARKER)
        while found != -1:
            block_offset = base + found - MARKER_OFFSET
            if block_offset >= 0:
                offsets.append(block_offset)
                if len(offsets) > max_records:
                    raise TelemetryError("The file contains too many GPS records.")
            found = window.find(MARKER, found + 1)
        tail = window[-overlap:]
        position += len(chunk)
    return offsets


def read_record_slots(
    f: BinaryIO, file_size: int, *, chunk_bytes: int = SCAN_CHUNK_BYTES
) -> tuple[list[RecordSlot], str]:
    """Decode every `freeGPS ` block in recording order.

    Returns the slots and `"index"` or `"scan"` naming how blocks were found.
    """
    offsets = read_gps_index(f, file_size)
    source = "index"
    if offsets is None:
        offsets = scan_markers(f, file_size, chunk_bytes=chunk_bytes)
        source = "scan"
    slots = []
    for offset in offsets:
        block = _read_exact(f, offset, RECORD_BYTES)
        try:
            slots.append(RecordSlot(offset, decode_record(block, offset)))
        except InvalidRecord as exc:
            slots.append(RecordSlot(offset, None, exc.reason))
    return slots, source
