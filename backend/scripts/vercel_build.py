"""Vercel build step: bundle static ffmpeg/ffprobe for the MP4 pipeline.

Runs after dependencies are installed (`[tool.vercel.scripts] build`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import media_tools  # noqa: E402

if __name__ == "__main__":
    media_tools.install(media_tools.BUNDLED_DIR)
    print(f"ffmpeg {media_tools.FFMPEG_VERSION} installed in {media_tools.BUNDLED_DIR}")
