"""Bounded MP4 inspection and sampled frame extraction using FFmpeg tools."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import tempfile

MAX_VIDEO_BYTES = 1_000_000_000
MAX_VIDEO_SECONDS = 600
MAX_SAMPLED_FRAMES = 60
MAX_VIDEO_FRAMES = 18_001


class InvalidVideo(ValueError):
    pass


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float


def _run(args: list[str], timeout: int = 120) -> str:
    try:
        # Tool output stays on disk until bounded, including malicious metadata.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            result = subprocess.run(args, stdout=output, stderr=errors, timeout=timeout)
            if result.returncode:
                raise InvalidVideo("The video cannot be decoded.")
            if output.tell() > 4 * 1024 * 1024:
                raise InvalidVideo("Video metadata exceeds the inspection limit.")
            output.seek(0)
            return output.read().decode("utf-8")
    except subprocess.TimeoutExpired as exc:
        raise InvalidVideo("Video inspection or decoding timed out.") from exc


def inspect_video(path: str | Path) -> VideoInfo:
    path = Path(path)
    if not 0 < path.stat().st_size <= MAX_VIDEO_BYTES:
        raise InvalidVideo("Video must be nonempty and at most 1,000,000,000 bytes.")
    try:
        # ISO Base Media File Format signature; filenames and declared MIME are untrusted.
        with path.open("rb") as source:
            signature = source.read(12)
        if signature[4:8] != b"ftyp" or signature[8:12] == b"qt  ":
            raise InvalidVideo("Upload an MP4 container.")
        data = json.loads(_run([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format", "-of", "json", str(path)
        ], timeout=30))
        streams = [s for s in data["streams"] if s.get("codec_type") == "video"]
        if len(streams) != 1 or streams[0].get("codec_name") != "h264":
            raise InvalidVideo("MP4 must contain exactly one H.264 video stream.")
        stream = streams[0]
        width, height = int(stream["width"]), int(stream["height"])
        duration = float(data["format"]["duration"])
        fps = float(Fraction(stream["avg_frame_rate"]))
        peak_fps = float(Fraction(stream["r_frame_rate"]))
        if not (math.isfinite(duration) and 0 < duration <= MAX_VIDEO_SECONDS):
            raise InvalidVideo("Video duration must be at most 600 seconds.")
        if not (0 < min(width, height) <= 1080 and max(width, height) <= 1920):
            raise InvalidVideo("Video dimensions must fit 1920 x 1080 in either orientation.")
        if not (0 < fps <= 30 and 0 < peak_fps <= 30):
            raise InvalidVideo("Video frame rate must be at most 30 fps.")
        return VideoInfo(duration, width, height, fps)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        if isinstance(exc, InvalidVideo):
            raise
        raise InvalidVideo("Invalid or unsupported video metadata.") from exc


def sampled_frames(path: str | Path):
    """Yield (actual relative PTS seconds, JPEG bytes), with bounded decoded output.

    FFprobe emits only timestamps; FFmpeg decodes sequentially to a temporary
    directory. At most 60 JPEGs are stored, and only one is read into memory.
    """
    info = inspect_video(path)
    frames = json.loads(_run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)
    ]))["frames"]
    if not frames or len(frames) > MAX_VIDEO_FRAMES:
        raise InvalidVideo("Invalid video frame count.")
    try:
        pts = [float(f["best_effort_timestamp_time"]) for f in frames]
    except (KeyError, ValueError) as exc:
        raise InvalidVideo("Video frames must have timestamps.") from exc
    times = [p - pts[0] for p in pts]
    if any(not math.isfinite(t) or t < 0 or t > info.duration + 0.1 for t in times):
        raise InvalidVideo("Invalid video timestamps.")
    if any(b < a for a, b in zip(times, times[1:])):
        raise InvalidVideo("Video timestamps must be ordered.")
    count = min(MAX_SAMPLED_FRAMES, len(times))
    # Choose real frames nearest evenly spaced timestamps, including both ends.
    import bisect
    indices = sorted({min(bisect.bisect_left(times, times[-1] * i / max(count - 1, 1)), len(times)-1)
                      for i in range(count)})
    expression = "+".join(f"eq(n,{i})" for i in indices)
    with tempfile.TemporaryDirectory(prefix="infrascan-frames-") as directory:
        _run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-protocol_whitelist", "file,pipe", "-threads", "2", "-i", str(path),
              "-map", "0:v:0", "-vf", f"select='{expression}'", "-vsync", "0",
              "-frames:v", str(count), "-q:v", "2", str(Path(directory) / "%03d.jpg")])
        files = sorted(Path(directory).glob("*.jpg"))
        if len(files) != len(indices):
            raise InvalidVideo("Could not decode all selected frames.")
        for index, file in zip(indices, files):
            yield times[index], file.read_bytes()
