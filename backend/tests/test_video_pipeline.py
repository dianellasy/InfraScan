"""MP4 contract evaluation, FFmpeg sampling, and synthetic seed clips."""

from __future__ import annotations

import copy
import io
import shutil
import subprocess
import sys
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import pytest

from a119_seed_support import ffmpeg_clip, requires_ffmpeg
from app.api.video_validation import (
    EmptyVideo,
    MissingGps,
    VideoTooLarge,
    looks_like_iso_bmff,
    validate_video_upload,
)
from inference.telemetry import locate_frame, read_a119_track
from inference.video import (
    MAX_SAMPLED_FRAMES,
    MediaToolError,
    VideoRejected,
    evaluate_packets,
    evaluate_probe,
    extract_frames,
    inspect_video,
    run_bounded,
    select_sample_pts,
)

PROBE = {
    "format": {"duration": "12.000000", "tags": {"major_brand": "isom"}},
    "streams": [
        {
            "index": 0, "codec_type": "video", "codec_name": "h264", "profile": "High",
            "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "avg_frame_rate": "30/1",
            "disposition": {"attached_pic": 0},
        },
        {"index": 1, "codec_type": "audio", "codec_name": "aac"},
    ],
}


def probe(**stream_changes) -> dict:
    report = copy.deepcopy(PROBE)
    report["streams"][0].update(stream_changes)
    return report


# --- ffprobe report contract ---


def test_a119_1080p_h264_report_is_accepted() -> None:
    info = evaluate_probe(PROBE)
    assert (info.width, info.height, info.average_fps, info.has_audio) == (1920, 1080, 30.0, True)
    assert info.duration_seconds == 12.0


@pytest.mark.parametrize(
    "changes",
    [
        {"width": 1080, "height": 1920},
        {"width": 1280, "height": 720},
        {"width": 2560, "height": 1440},
        {"width": 1440, "height": 2560},
        {"profile": "Constrained Baseline"},
        {"profile": "Main"},
        {"pix_fmt": "yuvj420p"},
        {"avg_frame_rate": "30000/1001"},
    ],
)
def test_accepted_variants(changes: dict) -> None:
    evaluate_probe(probe(**changes))


@pytest.mark.parametrize(
    ("changes", "kind", "code"),
    [
        ({"codec_name": "hevc", "profile": "Main"}, "unsupported", "UNSUPPORTED_MEDIA_FORMAT"),
        ({"codec_name": "mpeg4"}, "unsupported", "UNSUPPORTED_MEDIA_FORMAT"),
        ({"profile": "High 10", "pix_fmt": "yuv420p10le"}, "unsupported", "UNSUPPORTED_MEDIA_FORMAT"),
        ({"pix_fmt": "yuv422p"}, "unsupported", "UNSUPPORTED_MEDIA_FORMAT"),
        ({"width": 1920, "height": 1200}, "invalid", "INVALID_VIDEO_DIMENSIONS"),
        ({"width": 2304, "height": 1296}, "invalid", "INVALID_VIDEO_DIMENSIONS"),
        ({"width": 2560, "height": 1600}, "invalid", "INVALID_VIDEO_DIMENSIONS"),
        ({"width": 2592, "height": 1440}, "invalid", "INVALID_VIDEO_DIMENSIONS"),
        ({"width": 1440, "height": 2592}, "invalid", "INVALID_VIDEO_DIMENSIONS"),
        ({"width": 3840, "height": 2160}, "invalid", "INVALID_VIDEO_DIMENSIONS"),
        ({"width": 0}, "invalid", "INVALID_VIDEO"),
        ({"avg_frame_rate": "60/1"}, "invalid", "INVALID_VIDEO_FRAME_RATE"),
        ({"avg_frame_rate": "0/0"}, "invalid", "INVALID_VIDEO_FRAME_RATE"),
    ],
)
def test_rejected_streams(changes: dict, kind: str, code: str) -> None:
    with pytest.raises(VideoRejected) as caught:
        evaluate_probe(probe(**changes))
    assert (caught.value.kind, caught.value.code) == (kind, code)


@pytest.mark.parametrize(
    ("duration", "accepted"),
    [("600.000000", True), ("600.500000", True), ("600.600000", False), ("0", False), ("N/A", False)],
)
def test_duration_limits(duration: str, accepted: bool) -> None:
    report = copy.deepcopy(PROBE)
    report["format"]["duration"] = duration
    if accepted:
        evaluate_probe(report)
    else:
        with pytest.raises(VideoRejected) as caught:
            evaluate_probe(report)
        assert caught.value.code == "INVALID_VIDEO_DURATION"


def test_multiple_video_streams_are_unsupported_but_cover_art_is_ignored() -> None:
    report = copy.deepcopy(PROBE)
    report["streams"].append({**report["streams"][0], "index": 2})
    with pytest.raises(VideoRejected) as caught:
        evaluate_probe(report)
    assert caught.value.code == "UNSUPPORTED_MEDIA_FORMAT"

    report["streams"][-1]["disposition"] = {"attached_pic": 1}
    assert evaluate_probe(report).stream_index == 0


def test_quicktime_brand_and_empty_report() -> None:
    report = copy.deepcopy(PROBE)
    report["format"]["tags"]["major_brand"] = "qt  "
    with pytest.raises(VideoRejected) as caught:
        evaluate_probe(report)
    assert caught.value.kind == "unsupported"
    with pytest.raises(VideoRejected) as caught:
        evaluate_probe({"format": {}, "streams": []})
    assert caught.value.code == "INVALID_VIDEO"


# --- Packet timeline ---


def packets(pts: list[int], flags: str = "K__") -> str:
    return "\n".join(f"{value},{flags}" for value in pts)


def test_packet_timeline_uses_real_pts_and_skips_discarded_packets() -> None:
    csv = packets([0, 512, 1024]) + "\n-512,K_D\n" + packets([1536])
    timeline = evaluate_packets(csv, Fraction(1, 15360))
    assert timeline.pts == (0, 512, 1024, 1536)
    assert timeline.seconds(1024) == pytest.approx(1024 / 15360)


def test_peak_frame_rate_catches_bursts_hidden_by_the_average() -> None:
    # 30 fps on average over 2 s, but 45 frames inside the first second.
    burst = [i * 300 for i in range(45)] + [15360 + i * 1000 for i in range(15)]
    with pytest.raises(VideoRejected) as caught:
        evaluate_packets(packets(burst), Fraction(1, 15360))
    assert caught.value.code == "INVALID_VIDEO_FRAME_RATE"


def test_variable_frame_rate_at_or_below_30_is_accepted() -> None:
    pts = [0, 33, 66, 400, 1100, 1500, 2100, 3000, 3100]
    assert evaluate_packets(packets(pts), Fraction(1, 1000)).peak_frames_per_second == 4


@pytest.mark.parametrize("csv", ["", "N/A,K__", "abc,K__"])
def test_unreadable_packet_lists_are_invalid(csv: str) -> None:
    with pytest.raises(VideoRejected) as caught:
        evaluate_packets(csv, Fraction(1, 1000))
    assert caught.value.code == "INVALID_VIDEO"


# --- Sampling ---


def test_sampling_keeps_short_clips_whole_and_caps_long_ones() -> None:
    assert select_sample_pts([3, 1, 2, 2]) == [1, 2, 3]
    pts = list(range(0, 18_000 * 512, 512))  # 10 min at 30 fps
    chosen = select_sample_pts(pts)
    assert len(chosen) == MAX_SAMPLED_FRAMES
    assert chosen[0] == pts[0] and chosen[-1] == pts[-1]
    assert set(chosen) <= set(pts)
    gaps = {b - a for a, b in zip(chosen, chosen[1:])}
    assert max(gaps) - min(gaps) <= 512


# --- Bounded subprocesses ---


def test_run_bounded_kills_on_output_overflow() -> None:
    with pytest.raises(MediaToolError, match="stdout exceeded"):
        run_bounded(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100000)"],
            timeout=30, max_stdout=1000,
        )


def test_run_bounded_times_out_and_reports_exit_codes() -> None:
    with pytest.raises(MediaToolError, match="timed out"):
        run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5, max_stdout=10)
    with pytest.raises(MediaToolError, match="exited with 3"):
        run_bounded([sys.executable, "-c", "raise SystemExit(3)"], timeout=30, max_stdout=10)


def test_child_tools_do_not_inherit_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "do-not-leak")
    out, _ = run_bounded(
        [sys.executable, "-c", "import os; print(sorted(os.environ))"], timeout=30, max_stdout=4096
    )
    assert b"R2_SECRET_ACCESS_KEY" not in out


def test_iso_bmff_sniffing() -> None:
    assert looks_like_iso_bmff(b"\0\0\0\x18ftypisom\0\0\0\0")
    assert not looks_like_iso_bmff(b"\xff\xd8\xff\xe0\0\x10JFIF\0\x01\x01\0")
    assert not looks_like_iso_bmff(b"\0\0\0\x08ftypisom")
    assert not looks_like_iso_bmff(b"")


def test_upload_validation_rejects_empty_and_non_mp4_before_tools() -> None:
    with pytest.raises(EmptyVideo):
        validate_video_upload(io.BytesIO(b""), 0)
    with pytest.raises(VideoRejected) as caught:
        validate_video_upload(io.BytesIO(b"\xff\xd8\xff\xe0" + bytes(64)), 0)
    assert caught.value.kind == "unsupported"
    with pytest.raises(VideoRejected) as caught:
        validate_video_upload(io.BytesIO(b"\0\0\0\x14ftypqt  \0\0\0\0qt  "), 0)
    assert "QuickTime" in caught.value.message


def test_upload_copy_is_capped_and_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    source = io.BytesIO(b"\0\0\0\x18ftypisom\0\0\0\0isom" + bytes(5000))
    with pytest.raises(VideoTooLarge):
        validate_video_upload(source, 0, max_bytes=1000)
    assert list(tmp_path.iterdir()) == []
    assert source.tell() == 0


# --- Generated clips through real FFmpeg ---


@requires_ffmpeg
@pytest.mark.parametrize(
    ("scenario", "options", "source"),
    [
        ("standard", {"audio": True}, "index"),
        ("cold-start", {}, "index"),
        ("southeast", {}, "index"),
        ("north-crossing", {}, "index"),
        ("duplicate", {"seconds": 8}, "index"),
        ("corrupt-block", {"seconds": 8}, "index"),
        ("short-gap", {"seconds": 10}, "index"),
        ("standard", {"no_index": True}, "scan"),
        ("broken-index", {"seconds": 6}, "scan"),
    ],
)
def test_seed_clip_round_trips_through_inspection_and_parser(
    a119_seed, scenario: str, options: dict, source: str
) -> None:
    path, manifest = a119_seed(scenario, **options)
    info, timeline = inspect_video(path)
    assert (info.width, info.height) == (640, 360)
    assert info.has_audio is options.get("audio", False)
    assert info.duration_seconds == pytest.approx(options.get("seconds", 4), abs=0.1)
    assert timeline.peak_frames_per_second <= 30

    track = read_a119_track(path, manifest["camera_utc_offset_minutes"], info.duration_seconds)
    assert track.source == source
    expected = manifest["expected_first_fix"]
    assert track.first_fix.latitude == pytest.approx(expected["latitude"], abs=1e-9)
    assert track.first_fix.longitude == pytest.approx(expected["longitude"], abs=1e-9)
    assert track.first_fix.recorded_at.isoformat().replace("+00:00", "Z") == expected["recorded_at_utc"]
    assert track.first_fix.frame_offset_seconds == expected["slot"]

    written = [r for r in manifest["records"] if r["status"] == "A" and not r.get("corrupt")]
    unique = [r for r in written if "duplicate_of" not in r]
    assert len(track.fixes) == len(unique)
    for fix, record in zip(track.fixes, unique):
        assert fix.frame_offset_seconds == record["slot"]
        assert fix.latitude == pytest.approx(record["written"]["latitude"], abs=1e-9)
        assert fix.longitude == pytest.approx(record["written"]["longitude"], abs=1e-9)
        assert fix.course_deg == pytest.approx(record["written"]["course_deg"], abs=1e-4)
    assert track.duplicate_count == (scenario == "duplicate")
    assert track.invalid_count == (scenario == "corrupt-block")


@requires_ffmpeg
def test_seed_frames_decode_at_real_timestamps_and_long_gaps_have_no_gps(
    a119_seed, tmp_path: Path
) -> None:
    path, manifest = a119_seed("long-gap", seconds=12)
    info, timeline = inspect_video(path)
    selected = select_sample_pts(timeline.pts)
    frames = extract_frames(path, info, timeline, selected, tmp_path)
    assert len(frames) == MAX_SAMPLED_FRAMES
    assert [frame.pts for frame in frames] == selected
    assert frames[0].timestamp_seconds == 0.0
    assert frames[-1].timestamp_seconds == pytest.approx(12 - 1 / 30, abs=1e-6)
    assert all(frame.path.read_bytes()[:2] == b"\xff\xd8" for frame in frames)

    track = read_a119_track(path, manifest["camera_utc_offset_minutes"], info.duration_seconds)
    located = {round(f.timestamp_seconds, 3): locate_frame(track.fixes, f.timestamp_seconds) for f in frames}
    assert located[0.0].method == "exact"
    # Slots 3-10 are void: frames well inside the tunnel get no position.
    assert all(located[t] is None for t in located if 4.2 <= t <= 9.8)
    assert all(located[t] is not None for t in located if t >= 10.2)
    assert located[max(located)].method == "nearest"


@requires_ffmpeg
def test_no_gps_and_all_void_seeds_are_missing_gps(a119_seed) -> None:
    path, _ = a119_seed("no-gps")
    with open(path, "rb") as source, pytest.raises(MissingGps):
        validate_video_upload(source, -480)


@requires_ffmpeg
def test_upload_validation_derives_submission_from_first_fix(a119_seed) -> None:
    path, manifest = a119_seed("cold-start")
    with open(path, "rb") as source:
        validated = validate_video_upload(source, -480)
        assert source.tell() == 0
    first = validated.track.first_fix
    assert first.recorded_at == datetime.fromisoformat(manifest["expected_first_fix"]["recorded_at_utc"])
    assert first.latitude == pytest.approx(manifest["expected_first_fix"]["latitude"])


LAVFI = ["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=1"]


@requires_ffmpeg
@pytest.mark.parametrize(
    ("args", "kind", "code"),
    [
        (LAVFI + ["-c:v", "libx265", "-x265-params", "log-level=none", "-tag:v", "hvc1", "-f", "mp4"],
         "unsupported", "UNSUPPORTED_MEDIA_FORMAT"),
        (["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=60:duration=1", "-c:v", "libx264", "-f", "mp4"],
         "invalid", "INVALID_VIDEO_FRAME_RATE"),
        (["-f", "lavfi", "-i", "testsrc2=size=2560x1600:rate=1:duration=1", "-c:v", "libx264", "-f", "mp4"],
         "invalid", "INVALID_VIDEO_DIMENSIONS"),
        (LAVFI + ["-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30:duration=1",
                  "-map", "0", "-map", "1", "-c:v", "libx264", "-f", "mp4"],
         "unsupported", "UNSUPPORTED_MEDIA_FORMAT"),
    ],
    ids=["h265", "60fps", "above-1440p", "two-video-streams"],
)
def test_real_files_outside_the_contract_are_rejected(
    tmp_path: Path, args: list[str], kind: str, code: str
) -> None:
    path = ffmpeg_clip(tmp_path / "clip.mp4", *args)
    with pytest.raises(VideoRejected) as caught:
        inspect_video(path)
    assert (caught.value.kind, caught.value.code) == (kind, code)


@requires_ffmpeg
@pytest.mark.parametrize("size", ["2560x1440", "1440x2560"])
def test_real_1440p_file_is_accepted_and_decoded_at_full_size(tmp_path: Path, size: str) -> None:
    from PIL import Image

    path = ffmpeg_clip(
        tmp_path / "clip.mp4", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mp4",
    )
    info, timeline = inspect_video(path)
    assert f"{info.width}x{info.height}" == size
    (out := tmp_path / "frames").mkdir()
    [frame] = extract_frames(path, info, timeline, timeline.pts[:1], out)
    with Image.open(frame.path) as image:
        assert image.size == (info.width, info.height)


@requires_ffmpeg
def test_truncated_mp4_is_invalid_video(a119_seed, tmp_path: Path) -> None:
    path, _ = a119_seed("standard")
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(path.read_bytes()[:50_000])
    with pytest.raises(VideoRejected) as caught:
        inspect_video(truncated)
    assert caught.value.code == "INVALID_VIDEO"


@requires_ffmpeg
@pytest.mark.skipif(shutil.which("exiftool") is None, reason="ExifTool is not installed")
@pytest.mark.parametrize("scenario", ["standard", "southeast", "cold-start"])
def test_exiftool_independently_decodes_seed_gps(a119_seed, scenario: str) -> None:
    from a119_seed_support import load_generator

    path, manifest = a119_seed(scenario)
    assert load_generator().validate_with_exiftool(path, manifest) == []


def test_generator_refuses_large_durations() -> None:
    from a119_seed_support import GENERATOR_PATH

    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--output-dir", "/nonexistent", "--seconds", "601"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
