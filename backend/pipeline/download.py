"""Download source videos from a URL using yt-dlp."""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

from ..config import DOWNLOAD_DIR


def download_video(url: str, job_id: str) -> Tuple[Path, str]:
    """Download `url` into the downloads dir, named after the job id.

    Returns (path_to_file, title). Caps the resolution at 1080p — Shorts are
    vertical and we re-encode anyway, so pulling 4K wastes time and disk.
    """
    import yt_dlp

    outtmpl = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")
    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get("title", "video")

    # After merging, the real file may differ from prepare_filename's extension,
    # so locate it by the job-id prefix.
    matches = sorted(DOWNLOAD_DIR.glob(f"{job_id}.*"))
    if not matches:
        raise RuntimeError("Download finished but no output file was found.")
    # Prefer a container we know ffmpeg likes.
    for ext in (".mp4", ".mkv", ".webm"):
        for m in matches:
            if m.suffix == ext:
                return m, title
    return matches[0], title
