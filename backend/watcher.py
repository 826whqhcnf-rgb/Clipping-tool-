"""Watch-folder automation: drop a video in, get posted Shorts + a links file out.

Flow per file that lands in data/watch/:
  1. Wait until the file stops growing (it may still be copying in).
  2. Run a normal auto-mode job using the saved campaign preset
     (data/campaign.json — created with defaults on first run).
  3. If a YouTube account is connected and the preset says auto_post,
     upload every clip.
  4. Write the clip titles + links to data/watch/processed/<name>.links.txt
     (and append URLs to data/watch/posted_links.txt), then move the source
     into processed/. Failures go to failed/ with an .error.txt beside them.

Enable inside the web app with CLIP_WATCH=1, or run standalone:
    python -m backend.watcher
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Dict

from .config import AUTO_MAX_LEN, AUTO_MIN_LEN, DATA_DIR, OUTPUT_DIR

WATCH_DIR = DATA_DIR / "watch"
PROCESSED_DIR = WATCH_DIR / "processed"
FAILED_DIR = WATCH_DIR / "failed"
PRESET_PATH = DATA_DIR / "campaign.json"
GLOBAL_LINKS = WATCH_DIR / "posted_links.txt"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}

DEFAULT_PRESET = {
    "num_clips": 5,
    "reframe": "face",            # blur | face | crop | pad
    "captions": True,
    "highlight": True,
    "caption_style": "karaoke",   # karaoke | boxed | clean
    "watermark": "",              # e.g. "@creatorhandle" (burned onto the video)
    "campaign_tags": [],           # e.g. ["#brand", "@brand"] — added to descriptions
    "language": None,
    "auto_post": True,             # post to YouTube when an account is connected
    "privacy": "unlisted",         # unlisted | public | private
}


def load_preset() -> dict:
    """Read data/campaign.json, creating it with defaults on first run."""
    if not PRESET_PATH.exists():
        PRESET_PATH.write_text(json.dumps(DEFAULT_PRESET, indent=2), encoding="utf-8")
        return dict(DEFAULT_PRESET)
    try:
        preset = {**DEFAULT_PRESET, **json.loads(PRESET_PATH.read_text(encoding="utf-8"))}
        return preset
    except Exception:
        print("[watcher] campaign.json is invalid JSON — using defaults")
        return dict(DEFAULT_PRESET)


def _campaign_tokens(preset: dict) -> list[str]:
    return [str(t).strip() for t in preset.get("campaign_tags") or [] if str(t).strip()]


def _merged_tags(clip: dict, preset: dict) -> list[str]:
    """Clip hashtags + campaign hashtags (deduped, cleaned), like the UI does."""
    extra = ["".join(ch for ch in t.lstrip("#").lower() if ch.isalnum() or ch == "_")
             for t in _campaign_tokens(preset)
             if not t.startswith("@") and not t.startswith("http")]
    out: list[str] = []
    for t in [*(clip.get("hashtags") or []), *extra]:
        if t and t not in out:
            out.append(t)
    return out[:15]


def _description(clip: dict, preset: dict) -> str:
    tags = " ".join(f"#{t}" for t in _merged_tags(clip, preset))
    mentions = " ".join(t for t in _campaign_tokens(preset)
                        if t.startswith("@") or t.startswith("http"))
    parts = [clip.get("title") or "", clip.get("reason") or "", tags, mentions]
    return "\n\n".join(p for p in parts if p)


def _process_file(path: Path, preset: dict) -> None:
    """Run one video through the pipeline; post clips; write the links file."""
    from . import youtube as yt
    from .jobs import store, _run_job

    print(f"[watcher] processing {path.name}")
    job = store.create(
        url="", mode="auto",
        reframe=preset["reframe"], captions=bool(preset["captions"]),
        highlight=bool(preset["highlight"]), caption_style=preset["caption_style"],
        watermark=(preset.get("watermark") or "")[:60],
        language=preset.get("language"),
        num_clips=max(1, min(10, int(preset["num_clips"]))),
        min_len=AUTO_MIN_LEN, max_len=AUTO_MAX_LEN,
    )
    job.source_path = str(path)
    job.title = path.stem
    _run_job(job)  # synchronous: watcher handles one file at a time

    if job.status != "done":
        raise RuntimeError(job.error or "job failed")

    post = bool(preset.get("auto_post")) and yt.is_connected()
    lines = [f"source: {path.name}", f"clips: {len(job.clips)}", ""]
    posted_urls: list[str] = []
    for clip in job.clips:
        lines.append(f"• {clip['title']}  ({clip['start']}–{clip['end']}s, score={clip['score']})")
        if post:
            try:
                video_id = yt.upload(
                    OUTPUT_DIR / clip["output"], clip["title"] or "Short",
                    _description(clip, preset), _merged_tags(clip, preset),
                    preset.get("privacy", "unlisted"),
                )
                url = f"https://youtu.be/{video_id}"
                posted_urls.append(url)
                lines.append(f"  {url}")
            except Exception as exc:  # keep going; the clip file still exists
                lines.append(f"  ⚠ upload failed: {str(exc)[:200]}")
        else:
            lines.append(f"  file: {clip['output']} (in data/outputs/)")
        lines.append("")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROCESSED_DIR / f"{path.stem}.links.txt").write_text("\n".join(lines), encoding="utf-8")
    if posted_urls:
        with GLOBAL_LINKS.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(posted_urls) + "\n")
    shutil.move(str(path), PROCESSED_DIR / path.name)
    print(f"[watcher] done: {path.name} -> {len(job.clips)} clips, {len(posted_urls)} posted")


def scan_once(prev_sizes: Dict[str, int]) -> Dict[str, int]:
    """One poll of the watch folder. Returns the size map for the next poll.

    A file is processed only when its size matches the previous poll (i.e. it
    has finished copying in).
    """
    for d in (WATCH_DIR, PROCESSED_DIR, FAILED_DIR):
        d.mkdir(parents=True, exist_ok=True)

    sizes: Dict[str, int] = {}
    for p in sorted(WATCH_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        size = p.stat().st_size
        if prev_sizes.get(p.name) == size:
            try:
                _process_file(p, load_preset())
            except Exception as exc:  # noqa: BLE001 — quarantine and continue
                print(f"[watcher] FAILED {p.name}: {exc}")
                FAILED_DIR.mkdir(parents=True, exist_ok=True)
                (FAILED_DIR / f"{p.stem}.error.txt").write_text(
                    f"{exc}\n\n{traceback.format_exc()}", encoding="utf-8")
                if p.exists():
                    shutil.move(str(p), FAILED_DIR / p.name)
        else:
            sizes[p.name] = size  # still growing (or new) — check again next poll
    return sizes


def run_forever(poll_seconds: int | None = None) -> None:
    poll = poll_seconds or int(os.environ.get("CLIP_WATCH_INTERVAL", "15"))
    load_preset()  # ensure campaign.json exists so the user can edit it
    print(f"[watcher] watching {WATCH_DIR} every {poll}s — preset: {PRESET_PATH}")
    prev: Dict[str, int] = {}
    while True:
        try:
            prev = scan_once(prev)
        except Exception as exc:  # never let the loop die
            print(f"[watcher] scan error: {exc}")
        time.sleep(poll)


_started = False


def start_background() -> None:
    """Start the watcher inside the web app (idempotent)."""
    global _started
    if not _started:
        _started = True
        threading.Thread(target=run_forever, daemon=True, name="clip-watcher").start()


if __name__ == "__main__":
    run_forever()
