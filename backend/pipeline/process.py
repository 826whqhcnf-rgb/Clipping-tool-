"""Orchestrates the pipeline for a job: source -> (transcribe) -> clip(s)."""
from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List

from ..config import DOWNLOAD_DIR, WORK_DIR
from ..utils import ffprobe_duration, run
from .download import download_video
from .highlights import Highlight, find_highlights, snap_to_speech
from .render import render_clip, slice_words


def _extract_audio(source: Path, dest: Path, start=None, end=None) -> None:
    """Extract mono 16 kHz wav (optionally only a [start, end] segment)."""
    cmd = ["ffmpeg", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(dest)]
    run(cmd)


def process_job(job, update: Callable[..., None]) -> None:
    """Run the full pipeline for one job, reporting progress via `update`."""
    if job.mode == "generate":
        from .generate import generate_clips  # deferred: needs TTS/LLM deps
        generate_clips(job, update)
        return

    work_root = WORK_DIR / job.id
    work_root.mkdir(parents=True, exist_ok=True)

    # 1. Obtain the source video -------------------------------------------
    if job.source_path:
        source = Path(job.source_path)
        title = job.title or "upload"
        update(stage="download", progress=10, message="Using uploaded file…")
    else:
        update(stage="download", progress=5, message="Downloading video…")
        source, title = download_video(job.url, job.id)
        update(title=title)
    duration = ffprobe_duration(source)

    try:
        # 2. Decide segments + transcribe --------------------------------------
        from .transcribe import transcribe  # deferred so the model loads lazily

        def _progress_band(lo: int, hi: int):
            """Map transcription fraction (0..1) into a progress band + %."""
            def cb(frac: float) -> None:
                pct = lo + int((hi - lo) * frac)
                update(progress=pct, message=f"Transcribing speech… {int(frac * 100)}%")
            return cb

        def _safe_transcribe(audio, lo, hi):
            """Transcribe, but never let a failure kill the job — skip captions instead."""
            try:
                return transcribe(audio, language=job.language, progress=_progress_band(lo, hi))
            except Exception as exc:  # noqa: BLE001
                update(warning=f"Captions skipped — {str(exc)[:160]}")
                return []

        if job.mode == "auto":
            # Auto always needs the whole transcript to find highlights.
            update(stage="audio", progress=20, message="Extracting audio…")
            audio = work_root / "audio.wav"
            _extract_audio(source, audio)
            update(stage="transcribe", progress=30, message="Transcribing speech…")
            words = _safe_transcribe(audio, 30, 50)

            update(stage="analyze", progress=50, message="Finding the best moments…")
            highlights = find_highlights(
                words, num_clips=job.num_clips,
                min_len=job.min_len, max_len=job.max_len, duration=duration,
            )
            if not highlights:
                raise RuntimeError("No clip-worthy moments were found in this video.")
            # Snap windows to word boundaries so clips never start mid-word.
            for h in highlights:
                h.start, h.end = snap_to_speech(words, h.start, h.end, duration)
            # Words are absolute; slice per clip at render time.
            clip_words = [slice_words(words, h.start, h.end) if job.captions else []
                          for h in highlights]
        else:
            start = job.start if job.start is not None else 0.0
            end = job.end if job.end is not None else duration
            highlights = [Highlight(start, end, title, None, "Manual selection")]
            # Manual: only transcribe the selected segment (much faster on long videos).
            seg_words: List[dict] = []
            if job.captions:
                update(stage="audio", progress=25, message="Extracting audio…")
                audio = work_root / "audio.wav"
                _extract_audio(source, audio, start=start, end=end)
                update(stage="transcribe", progress=45, message="Transcribing speech…")
                seg_words = _safe_transcribe(audio, 45, 58)  # clip-relative
            clip_words = [seg_words]

        # 3. Render clips (in parallel — big wall-clock win on multi-clip jobs) --
        total = len(highlights)
        update(stage="render", progress=60, message=f"Rendering {total} clip(s)…")

        def _render_one(i: int, h, cw) -> dict:
            clip_id = f"{job.id}-{i + 1}"
            out_name = render_clip(
                source, clip_id, h.start, h.end, cw,
                job.reframe, job.captions, job.highlight, job.caption_style,
                watermark=(job.watermark or ""),
            )
            return {
                "id": clip_id, "index": i + 1, "title": h.title,
                "score": h.score, "reason": h.reason, "hashtags": h.hashtags,
                "start": round(h.start, 2), "end": round(h.end, 2),
                "output": out_name,
            }

        workers = int(os.environ.get("CLIP_RENDER_WORKERS",
                                     min(2, os.cpu_count() or 1)))
        results: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(_render_one, i, h, cw)
                       for i, (h, cw) in enumerate(zip(highlights, clip_words))]
            for done, fut in enumerate(as_completed(futures), 1):
                res = fut.result()  # propagate render errors
                results[res["index"]] = res
                update(progress=60 + int(38 * done / max(1, total)),
                       message=f"Rendered {done} of {total}…",
                       clips=[results[k] for k in sorted(results)])

        clips = [results[k] for k in sorted(results)]
        update(stage="done", progress=100, message="Done!", clips=clips)
    finally:
        # Free disk: drop the work dir and the downloaded/uploaded source.
        # Never delete user-provided input files (those live in INPUT_DIR).
        shutil.rmtree(work_root, ignore_errors=True)
        try:
            if source.is_relative_to(DOWNLOAD_DIR) and source.exists():
                source.unlink()
        except OSError:
            pass
