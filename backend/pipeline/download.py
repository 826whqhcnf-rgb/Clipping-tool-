"""Download source videos from a URL using yt-dlp.

Note on YouTube + cloud servers: YouTube often blocks downloads from
datacenter IPs (Codespaces, most VPS hosts) with a "Sign in to confirm you're
not a bot" error. We try the player clients that tend to slip past this, and
support an optional cookies file (CLIP_COOKIES_FILE) for when they don't.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from ..config import DOWNLOAD_DIR

# These tell yt-dlp to impersonate non-web YouTube clients, which are less
# likely to hit the bot wall than the default web client.
_YT_PLAYER_CLIENTS = ["android", "ios", "tv", "web"]

_BOT_HINT = (
    "YouTube blocked this download with a bot check. This usually happens when "
    "the server runs on a cloud/datacenter IP (like Codespaces). Fixes: supply "
    "a cookies file via the CLIP_COOKIES_FILE env var (see the README), or run "
    "the tool from a home/residential network."
)


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
        "extractor_args": {"youtube": {"player_client": _YT_PLAYER_CLIENTS}},
    }

    # Optional cookies file to get past login / bot checks.
    cookies = os.environ.get("CLIP_COOKIES_FILE")
    if cookies and Path(cookies).exists():
        ydl_opts["cookiefile"] = cookies

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")
    except Exception as exc:  # noqa: BLE001 - surface a readable reason to the UI
        message = str(exc)
        if "confirm you" in message or "not a bot" in message or "Sign in" in message:
            raise RuntimeError(_BOT_HINT) from exc
        raise RuntimeError(f"Download failed: {message}") from exc

    # After merging, the real file may differ from prepare_filename's extension,
    # so locate it by the job-id prefix.
    matches = sorted(DOWNLOAD_DIR.glob(f"{job_id}.*"))
    if not matches:
        raise RuntimeError("Download finished but no output file was found.")
    for ext in (".mp4", ".mkv", ".webm"):
        for m in matches:
            if m.suffix == ext:
                return m, title
    return matches[0], title
