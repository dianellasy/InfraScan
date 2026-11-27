"""Generate small synthetic A119 Mini 2-like clips for tests (requires FFmpeg)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GENERATOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_a119_mini2_seed.py"

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required for generated-clip tests",
)


def load_generator():
    name = "generate_a119_mini2_seed"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, GENERATOR_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def generate_seed(
    output_dir: Path,
    scenario: str = "standard",
    *,
    seconds: int = 4,
    width: int = 640,
    height: int = 360,
    fps: int = 30,
    audio: bool = False,
    no_index: bool = False,
    offset_minutes: int = -480,
) -> tuple[Path, dict]:
    generator = load_generator()
    argv = [
        "--output-dir", str(output_dir), "--scenario", scenario, "--seconds", str(seconds),
        "--width", str(width), "--height", str(height), "--fps", str(fps),
        "--camera-utc-offset-minutes", str(offset_minutes),
    ]
    if audio:
        argv.append("--audio")
    if no_index:
        argv.append("--no-index")
    path, manifest_path, _ = generator.generate(generator.parse_args(argv))
    return path, json.loads(manifest_path.read_text(encoding="utf-8"))


def ffmpeg_clip(path: Path, *args: str) -> Path:
    """Encode a tiny lavfi clip with the given FFmpeg output arguments."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args, str(path)],
        check=True,
        timeout=120,
    )
    return path
