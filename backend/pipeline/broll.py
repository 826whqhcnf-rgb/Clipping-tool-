"""Fetch free stock B-roll from Pexels to use as a Short's background.

Optional: needs a free PEXELS_API_KEY (pexels.com/api). Without it, or on any
failure, callers fall back to the gradient background.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def fetch_broll(query: str, dest: Path, min_height: int = 1280) -> Optional[Path]:
    """Search Pexels for portrait video matching `query` and download it to `dest`.

    Returns the path, or None if unavailable (no key, no result, network error).
    """
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    try:
        import httpx

        resp = httpx.get(
            "https://api.pexels.com/videos/search",
            params={"query": query or "finance", "per_page": 12,
                    "orientation": "portrait", "size": "medium"},
            headers={"Authorization": key}, timeout=20,
        )
        resp.raise_for_status()
        videos = resp.json().get("videos", [])

        # Prefer a tall, reasonably high-res file.
        link = None
        for v in videos:
            files = sorted(v.get("video_files", []),
                           key=lambda f: (f.get("height") or 0), reverse=True)
            for f in files:
                if (f.get("height") or 0) >= min_height and f.get("link"):
                    link = f["link"]
                    break
            if link:
                break
        if not link and videos:
            files = videos[0].get("video_files", [])
            link = files[0]["link"] if files else None
        if not link:
            return None

        with httpx.stream("GET", link, timeout=90, follow_redirects=True) as r:
            r.raise_for_status()
            with open(dest, "wb") as out:
                for chunk in r.iter_bytes(1 << 16):
                    out.write(chunk)
        return dest if dest.exists() and dest.stat().st_size > 1000 else None
    except Exception:
        return None
