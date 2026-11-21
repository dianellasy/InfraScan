"""Bounded ffprobe/FFmpeg helpers for the supported A119 Mini 2 MP4 contract.

Tools only ever see a local path. Every invocation uses an argument array (no
shell), stdin closed, the demuxer forced to MP4, only the `file` protocol
whitelisted, a timeout, capped captured output, and a minimal environment so
credentials are never inherited by child processes.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

FFPROBE_BIN = os.environ.get("INFRASCAN_FFPROBE", "ffprobe")
FFMPEG_BIN = os.environ.get("INFRASCAN_FFMPEG", "ffmpeg")

MAX_VIDEO_BYTES = 1_000_000_000
MAX_DURATION_SECONDS = 600.0
# Container durations can round slightly past a camera's 10-minute loop.
DURATION_TOLERANCE_SECONDS = 0.5
MAX_LONG_SIDE = 2560
MAX_SHORT_SIDE = 1440
MAX_FPS = 30
# One frame of timestamp jitter is tolerated inside any 1-second window.
MAX_FRAMES_PER_SECOND_WINDOW = MAX_FPS + 1
MAX_FRAMES = math.ceil((MAX_DURATION_SECONDS + DURATION_TOLERANCE_SECONDS) * MAX_FPS) + MAX_FPS
MAX_SAMPLED_FRAMES = 60

SUPPORTED_H264_PROFILES = frozenset({"Constrained Baseline", "Baseline", "Main", "High"})
SUPPORTED_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
UNSUPPORTED_BRANDS = frozenset({"qt  "})

PROBE_TIMEOUT_SECONDS = float(os.environ.get("INFRASCAN_FFPROBE_TIMEOUT_SECONDS", 60))
PACKET_TIMEOUT_SECONDS = float(os.environ.get("INFRASCAN_FFPROBE_PACKET_TIMEOUT_SECONDS", 180))
DECODE_TIMEOUT_SECONDS = float(os.environ.get("INFRASCAN_FFMPEG_TIMEOUT_SECONDS", 900))
MAX_PROBE_OUTPUT_BYTES = 1 << 20
MAX_PACKET_OUTPUT_BYTES = 4 << 20
MAX_LOG_OUTPUT_BYTES = 4 << 20
MAX_FRAME_JPEG_BYTES = 16 << 20

_COMMON_INPUT_ARGS = ("-protocol_whitelist", "file", "-f", "mp4")
_SHOWINFO_PTS = re.compile(rb"\[Parsed_showinfo_\d+ @ [^\]]+\] n:\s*(\d+) pts:\s*(-?\d+) ")


class MediaToolError(RuntimeError):
    """ffprobe/FFmpeg failed, timed out, or produced unbounded output."""


class VideoRejected(ValueError):
    """The file violates the supported MP4 contract.

    `kind` is "unsupported" (container/codec/profile) or "invalid" (corrupt or
    out-of-range); `code` and the message are stable API values.
    """

    def __init__(self, kind: str, code: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message


def _unsupported(message: str) -> VideoRejected:
    return VideoRejected("unsupported", "UNSUPPORTED_MEDIA_FORMAT", message)


def _invalid(code: str, message: str) -> VideoRejected:
    return VideoRejected("invalid", code, message)


def _corrupt() -> VideoRejected:
    return _invalid("INVALID_VIDEO", "The video could not be read as a valid MP4.")


@dataclass(frozen=True)
class VideoInfo:
    stream_index: int
    width: int
    height: int
    duration_seconds: float
    profile: str
    pixel_format: str
    average_fps: float
    has_audio: bool


@dataclass(frozen=True)
class VideoTimeline:
    time_base: Fraction
    first_pts: int
    pts: tuple[int, ...]
    peak_frames_per_second: int

    def seconds(self, pts: int) -> float:
        return float((pts - self.first_pts) * self.time_base)


@dataclass(frozen=True)
class SampledFrame:
    pts: int
    timestamp_seconds: float
    path: Path


def _child_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}


def run_bounded(
    args: Sequence[str], *, timeout: float, max_stdout: int, max_stderr: int = 64 << 10
) -> tuple[bytes, bytes]:
    """Run a tool with capped stdout/stderr; raise MediaToolError on any failure."""
    proc = subprocess.Popen(
        list(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(),
    )
    overflow: list[str] = []

    def drain(stream, limit: int, sink: list[bytes], name: str) -> None:
        size = 0
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > limit:
                overflow.append(name)
                with contextlib.suppress(OSError):
                    proc.kill()
                break
            sink.append(chunk)
        stream.close()

    out: list[bytes] = []
    err: list[bytes] = []
    readers = [
        threading.Thread(target=drain, args=(proc.stdout, max_stdout, out, "stdout"), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, max_stderr, err, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise MediaToolError(f"{Path(args[0]).name} timed out after {timeout:g}s") from None
    finally:
        for reader in readers:
            reader.join(timeout=5)
    if overflow:
        raise MediaToolError(f"{Path(args[0]).name} {overflow[0]} exceeded its limit")
    if proc.returncode != 0:
        tail = b"".join(err)[-500:].decode("utf-8", "replace").strip()
        raise MediaToolError(f"{Path(args[0]).name} exited with {proc.returncode}: {tail}")
    return b"".join(out), b"".join(err)


def _ratio(value: object) -> Fraction | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        ratio = Fraction(int(numerator), int(denominator))
    except (ValueError, ZeroDivisionError):
        return None
    return ratio if ratio > 0 else None


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def probe_video(path: Path) -> VideoInfo:
    try:
        stdout, _ = run_bounded(
            [
                FFPROBE_BIN, "-v", "error", "-hide_banner", *_COMMON_INPUT_ARGS,
                "-show_entries",
                "format=duration:format_tags=major_brand"
                ":stream=index,codec_type,codec_name,profile,width,height,pix_fmt,"
                "avg_frame_rate,duration:stream_disposition=attached_pic",
                "-of", "json", f"file:{path}",
            ],
            timeout=PROBE_TIMEOUT_SECONDS,
            max_stdout=MAX_PROBE_OUTPUT_BYTES,
        )
        report = json.loads(stdout)
    except (MediaToolError, ValueError):
        raise _corrupt() from None
    return evaluate_probe(report)


def evaluate_probe(report: object) -> VideoInfo:
    """Apply the MP4 contract to ffprobe JSON (separate for focused tests)."""
    if not isinstance(report, dict):
        raise _corrupt()
    fmt = report.get("format") or {}
    streams = report.get("streams") or []
    if not isinstance(fmt, dict) or not isinstance(streams, list):
        raise _corrupt()
    brand = (fmt.get("tags") or {}).get("major_brand")
    if isinstance(brand, str) and brand in UNSUPPORTED_BRANDS:
        raise _unsupported("QuickTime .mov files are not supported; upload an MP4.")

    video = [
        s for s in streams
        if isinstance(s, dict) and s.get("codec_type") == "video"
        and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not video:
        raise _corrupt()
    if len(video) != 1:
        raise _unsupported("The MP4 must contain exactly one video stream.")
    stream = video[0]
    codec = stream.get("codec_name")
    if codec == "hevc":
        raise _unsupported("H.265/HEVC is not supported; set the camera to H.264.")
    if codec != "h264":
        raise _unsupported("Only H.264 video is supported; set the camera to H.264.")
    profile = stream.get("profile")
    if profile not in SUPPORTED_H264_PROFILES:
        raise _unsupported("Only 8-bit H.264 Baseline, Main, or High profile is supported.")
    pixel_format = stream.get("pix_fmt")
    if pixel_format not in SUPPORTED_PIXEL_FORMATS:
        raise _unsupported("Only 8-bit 4:2:0 H.264 video is supported.")

    width, height = stream.get("width"), stream.get("height")
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise _corrupt()
    if max(width, height) > MAX_LONG_SIDE or min(width, height) > MAX_SHORT_SIDE:
        raise _invalid(
            "INVALID_VIDEO_DIMENSIONS",
            "Video must be at most 2560x1440; set the camera to 1440P or 1080P.",
        )

    duration = _finite_float(fmt.get("duration"))
    if duration is None:
        duration = _finite_float(stream.get("duration"))
    if duration is None or duration <= 0:
        raise _invalid("INVALID_VIDEO_DURATION", "Video duration must be greater than zero.")
    if duration > MAX_DURATION_SECONDS + DURATION_TOLERANCE_SECONDS:
        raise _invalid(
            "INVALID_VIDEO_DURATION",
            "Video must be at most 10 minutes; set the loop length to 10 minutes or less.",
        )

    average_fps = _ratio(stream.get("avg_frame_rate"))
    if average_fps is None:
        raise _invalid("INVALID_VIDEO_FRAME_RATE", "Video frame rate must be positive.")
    if average_fps > MAX_FPS:
        raise _invalid(
            "INVALID_VIDEO_FRAME_RATE", "Video must be at most 30 fps; set the camera to 30 fps."
        )

    return VideoInfo(
        stream_index=int(stream.get("index", 0)),
        width=width,
        height=height,
        duration_seconds=duration,
        profile=profile,
        pixel_format=pixel_format,
        average_fps=float(average_fps),
        has_audio=any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in streams),
    )


def probe_timeline(path: Path, info: VideoInfo) -> VideoTimeline:
    """Read real presentation timestamps of the validated video stream."""
    try:
        header, _ = run_bounded(
            [
                FFPROBE_BIN, "-v", "error", "-hide_banner", *_COMMON_INPUT_ARGS,
                "-select_streams", str(info.stream_index),
                "-show_entries", "stream=time_base", "-of", "csv=p=0", f"file:{path}",
            ],
            timeout=PROBE_TIMEOUT_SECONDS,
            max_stdout=4096,
        )
        packets, _ = run_bounded(
            [
                FFPROBE_BIN, "-v", "error", "-hide_banner", *_COMMON_INPUT_ARGS,
                "-select_streams", str(info.stream_index),
                "-show_entries", "packet=pts,flags", "-of", "csv=p=0", f"file:{path}",
            ],
            timeout=PACKET_TIMEOUT_SECONDS,
            max_stdout=MAX_PACKET_OUTPUT_BYTES,
        )
    except MediaToolError:
        raise _corrupt() from None
    time_base = _ratio(header.decode("ascii", "replace").strip())
    if time_base is None:
        raise _corrupt()
    return evaluate_packets(packets.decode("ascii", "replace"), time_base)


def evaluate_packets(packet_csv: str, time_base: Fraction) -> VideoTimeline:
    pts_values: set[int] = set()
    for line in packet_csv.splitlines():
        if not line.strip():
            continue
        raw_pts, _, flags = line.partition(",")
        if "D" in flags:
            continue
        try:
            pts_values.add(int(raw_pts))
        except ValueError:
            raise _corrupt() from None
        if len(pts_values) > MAX_FRAMES:
            raise _invalid(
                "INVALID_VIDEO_FRAME_RATE", "Video has more frames than 10 minutes at 30 fps."
            )
    if not pts_values:
        raise _corrupt()
    ordered = tuple(sorted(pts_values))
    window = 1 / time_base
    peak = 0
    start = 0
    for end, value in enumerate(ordered):
        while (value - ordered[start]) >= window:
            start += 1
        peak = max(peak, end - start + 1)
    if peak > MAX_FRAMES_PER_SECOND_WINDOW:
        raise _invalid(
            "INVALID_VIDEO_FRAME_RATE", "Video must be at most 30 fps; set the camera to 30 fps."
        )
    return VideoTimeline(time_base, ordered[0], ordered, peak)


def inspect_video(path: Path) -> tuple[VideoInfo, VideoTimeline]:
    info = probe_video(path)
    return info, probe_timeline(path, info)


def select_sample_pts(pts: Sequence[int], max_frames: int = MAX_SAMPLED_FRAMES) -> list[int]:
    """Pick real PTS values nearest to evenly spaced targets, first and last included."""
    if not pts:
        return []
    ordered = sorted(set(pts))
    if len(ordered) <= max_frames:
        return ordered
    first, last = ordered[0], ordered[-1]
    chosen: list[int] = []
    cursor = 0
    for i in range(max_frames):
        target = first + (last - first) * Fraction(i, max_frames - 1)
        while cursor + 1 < len(ordered) and abs(ordered[cursor + 1] - target) <= abs(
            ordered[cursor] - target
        ):
            cursor += 1
        if not chosen or ordered[cursor] != chosen[-1]:
            chosen.append(ordered[cursor])
    return chosen


def extract_frames(
    path: Path, info: VideoInfo, timeline: VideoTimeline, selected: Sequence[int], out_dir: Path
) -> list[SampledFrame]:
    """Decode exactly the selected PTS values in one FFmpeg pass."""
    if not selected or len(selected) > MAX_SAMPLED_FRAMES:
        raise ValueError("Between 1 and 60 frames must be selected")
    expression = "+".join(f"eq(pts,{value})" for value in selected)
    try:
        _, log = run_bounded(
            [
                FFMPEG_BIN, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
                *_COMMON_INPUT_ARGS, "-copyts", "-i", f"file:{path}",
                "-map", f"0:{info.stream_index}", "-an", "-sn", "-dn",
                "-vf", f"select='{expression}',showinfo",
                "-fps_mode", "passthrough", "-frames:v", str(len(selected)),
                "-q:v", "2", "-f", "image2", str(out_dir / "%04d.jpg"),
            ],
            timeout=DECODE_TIMEOUT_SECONDS,
            max_stdout=4096,
            max_stderr=MAX_LOG_OUTPUT_BYTES,
        )
    except MediaToolError:
        raise _corrupt() from None
    decoded = [int(match.group(2)) for match in _SHOWINFO_PTS.finditer(log)]
    files = sorted(out_dir.glob("*.jpg"))
    if decoded != list(selected) or len(files) != len(selected):
        raise _invalid("INVALID_VIDEO", "Selected video frames could not be decoded.")
    frames = []
    for value, file in zip(selected, files):
        if file.stat().st_size > MAX_FRAME_JPEG_BYTES:
            raise _invalid("INVALID_VIDEO", "A decoded video frame was unexpectedly large.")
        frames.append(SampledFrame(value, timeline.seconds(value), file))
    return frames
