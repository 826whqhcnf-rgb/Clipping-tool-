"""Render a single vertical, optionally-captioned clip from a source video."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from ..config import OUTPUT_DIR, WORK_DIR
from ..utils import ffprobe_dimensions, run
from .captions import build_ass
from .facecrop import detect_face_center
from .reframe import reframe_filter, smart_crop_vf
from .transcribe import Word


def slice_words(words: List[Word], start: float, end: float) -> List[Word]:
    """Return the words spoken within [start, end], re-based to clip-relative time."""
    out: List[Word] = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        out.append({
            "start": max(0.0, w["start"] - start),
            "end": max(0.0, w["end"] - start),
            "text": w["text"],
        })
    return out


def build_clip_cmd(
    source: Path,
    out: Path,
    start: Optional[float],
    end: Optional[float],
    reframe_mode: str,
) -> List[str]:
    """ffmpeg command to trim the source and reframe it to vertical 9:16."""
    if reframe_mode == "face":
        # Keep the speaker in frame: detect their position, then crop around it.
        src_w, src_h = ffprobe_dimensions(source)
        center = detect_face_center(source, start or 0.0,
                                    end if end is not None else (start or 0.0) + 30.0)
        kind, fstr = "vf", smart_crop_vf(0.5 if center is None else center, src_w, src_h)
    else:
        kind, fstr = reframe_filter(reframe_mode)

    cmd: List[str] = ["ffmpeg", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(source)]

    if kind == "vf":
        cmd += ["-vf", fstr]
    else:
        cmd += ["-filter_complex", fstr, "-map", "[v]", "-map", "0:a?"]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        # Normalize loudness so every clip plays back at a consistent volume.
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]
    return cmd


def render_clip(
    source: Path,
    clip_id: str,
    start: float,
    end: float,
    words: List[Word],
    reframe: str,
    captions: bool,
    highlight: bool,
    caption_style: str = "karaoke",
) -> str:
    """Render one clip to OUTPUT_DIR and return its output filename."""
    work = WORK_DIR / clip_id
    work.mkdir(parents=True, exist_ok=True)

    vertical = work / "vertical.mp4"
    run(build_clip_cmd(source, vertical, start, end, reframe))

    if captions and words:
        ass = work / "captions.ass"
        ass.write_text(build_ass(words, highlight=highlight, preset=caption_style),
                       encoding="utf-8")
        # Run from the work dir so the subtitles filter gets a clean relative path.
        run(["ffmpeg", "-y", "-i", "vertical.mp4",
             "-vf", "subtitles=captions.ass",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "copy", "-movflags", "+faststart", "final.mp4"],
            cwd=str(work))
        final = work / "final.mp4"
    else:
        final = vertical

    out_name = f"{clip_id}.mp4"
    shutil.copy(final, OUTPUT_DIR / out_name)
    shutil.rmtree(work, ignore_errors=True)  # free disk; the output is saved
    return out_name
