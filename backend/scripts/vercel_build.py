"""Vercel build step: bundle static ffmpeg/ffprobe for the MP4 pipeline.

Runs after dependencies are installed (`[tool.vercel.scripts] build`).

The binaries add about 160 MB, which takes the function to about 500 MB, the
standard Python limit. New Vercel projects switch to "large functions" (up to
5 GB) automatically when a function is over it. If a deploy fails on size
anyway, set INFRASCAN_BUNDLE_FFMPEG=0: the first video job on each instance
then downloads the same pinned build to /tmp instead (see
inference/media_tools.py). Photos never need ffmpeg.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inference import media_tools  # noqa: E402

if __name__ == "__main__":
    if os.environ.get("INFRASCAN_BUNDLE_FFMPEG", "1") == "0":
        print("INFRASCAN_BUNDLE_FFMPEG=0: ffmpeg will be downloaded at runtime")
    else:
        media_tools.install(media_tools.BUNDLED_DIR)
        print(f"ffmpeg {media_tools.FFMPEG_VERSION} installed in {media_tools.BUNDLED_DIR}")
