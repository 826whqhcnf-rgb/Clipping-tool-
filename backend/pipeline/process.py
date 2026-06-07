"""Orchestrates the pipeline for a job: download -> (transcribe) -> clip(s)."""
from __future__ import annotations

from typing import Callable, List

from ..config import WORK_DIR
from ..utils import ffprobe_duration, run
from .download import download_video
from .highlights import Highlight, find_highlights
from .render import render_clip, slice_words


def process_job(job, update: Callable[..., None]) -> None:
    """Run the full pipeline for one job, reporting progress via `update`."""
    work_root = WORK_DIR / job.id
    work_root.mkdir(parents=True, exist_ok=True)

    # 1. Obtain the source video -------------------------------------------
    from pathlib import Path

    if job.source_path:
        source = Path(job.source_path)
        title = job.title or "upload"
        update(stage="download", progress=10, message="Using uploaded file…")
    else:
        update(stage="download", progress=5, message="Downloading video…")
        source, title = download_video(job.url, job.id)
        update(title=title)
    duration = ffprobe_duration(source)

    # 2. Transcribe (needed for captions, and always for auto highlights) ---
    words: List[dict] = []
    if job.captions or job.mode == "auto":
        update(stage="audio", progress=20, message="Extracting audio…")
        audio = work_root / "audio.wav"
        run(["ffmpeg", "-y", "-i", str(source),
             "-vn", "-ac", "1", "-ar", "16000", str(audio)])

        update(stage="transcribe", progress=30, message="Transcribing speech…")
        from .transcribe import transcribe  # deferred so the model loads lazily
        words = transcribe(audio, language=job.language)

    # 3. Decide which segments to cut --------------------------------------
    if job.mode == "auto":
        update(stage="analyze", progress=50, message="Finding the best moments…")
        highlights = find_highlights(
            words,
            num_clips=job.num_clips,
            min_len=job.min_len,
            max_len=job.max_len,
            duration=duration,
        )
        if not highlights:
            raise RuntimeError("No clip-worthy moments were found in this video.")
    else:
        start = job.start if job.start is not None else 0.0
        end = job.end if job.end is not None else duration
        highlights = [Highlight(start, end, title, None, "Manual selection")]

    # 4. Render every clip --------------------------------------------------
    clips: List[dict] = []
    total = len(highlights)
    for i, h in enumerate(highlights):
        update(
            stage="render",
            progress=60 + int(35 * i / max(1, total)),
            message=f"Rendering clip {i + 1} of {total}…",
        )
        clip_id = f"{job.id}-{i + 1}"
        sub = slice_words(words, h.start, h.end) if job.captions else []
        out_name = render_clip(
            source, clip_id, h.start, h.end, sub,
            job.reframe, job.captions, job.highlight,
        )
        clips.append({
            "id": clip_id,
            "index": i + 1,
            "title": h.title,
            "score": h.score,
            "reason": h.reason,
            "start": round(h.start, 2),
            "end": round(h.end, 2),
            "output": out_name,
        })
        update(clips=list(clips))  # surface clips to the UI as they finish

    update(stage="done", progress=100, message="Done!", clips=clips)
