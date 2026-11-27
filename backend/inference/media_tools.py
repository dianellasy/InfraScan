"""Where the ffmpeg and ffprobe executables come from.

Docker and local machines use the system tools on PATH. Vercel's Python
runtime has none, and bundling them (about 160 MB) would push the function
past Vercel's 225 MB limit for project files, so the first video job on each
instance downloads a pinned static build (42 MB) to /tmp. The archive is
checked against its SHA-256 before use. A `backend/bin/` with both tools, if
present, is used first. Photos never need ffmpeg.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import tarfile
import threading
import urllib.request
from pathlib import Path

FFMPEG_VERSION = "7.0.2"
FFMPEG_ARCHIVE_SHA256 = "abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67"
# johnvansickle.com moves a version from releases/ to old-releases/ once a newer one ships.
FFMPEG_ARCHIVE_URLS = (
    f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-{FFMPEG_VERSION}-amd64-static.tar.xz",
    f"https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-{FFMPEG_VERSION}-amd64-static.tar.xz",
)
TOOLS = ("ffmpeg", "ffprobe")

BUNDLED_DIR = Path(__file__).resolve().parents[1] / "bin"
RUNTIME_DIR = Path(os.environ.get("INFRASCAN_TOOLS_DIR", "/tmp/infrascan-ffmpeg"))

_lock = threading.Lock()


def tool_path(name: str) -> str:
    """Absolute path (or PATH name) of `ffmpeg` or `ffprobe`."""
    for directory in (BUNDLED_DIR, RUNTIME_DIR):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    if shutil.which(name) or not os.environ.get("VERCEL"):
        return name
    with _lock:
        if not (RUNTIME_DIR / name).is_file():
            install(RUNTIME_DIR)
    return str(RUNTIME_DIR / name)


def install(target_dir: Path) -> None:
    """Download the pinned static build and extract ffmpeg and ffprobe into `target_dir`."""
    archive = _download()
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if name not in TOOLS or not member.isfile():
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            partial = target_dir / f".{name}.partial"
            with open(partial, "wb") as out:
                shutil.copyfileobj(source, out)
            partial.chmod(partial.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            partial.replace(target_dir / name)
    missing = [name for name in TOOLS if not (target_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"ffmpeg archive did not contain {missing}")


def _download() -> bytes:
    errors = []
    for url in FFMPEG_ARCHIVE_URLS:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
        except OSError as exc:
            errors.append(f"{url}: {exc}")
            continue
        if hashlib.sha256(data).hexdigest() != FFMPEG_ARCHIVE_SHA256:
            errors.append(f"{url}: SHA-256 mismatch")
            continue
        return data
    raise RuntimeError("Could not download ffmpeg: " + "; ".join(errors))
