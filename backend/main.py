"""FastAPI app: REST endpoints + static frontend for the Shorts Clipper."""
from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    AUTO_MAX_CLIPS,
    AUTO_MAX_LEN,
    AUTO_MIN_LEN,
    FRONTEND_DIR,
    MAX_CLIP_SECONDS,
    OUTPUT_DIR,
)
from .jobs import start_job, store
from .utils import have_binary, parse_timestamp

app = FastAPI(title="Shorts Clipper")

VALID_REFRAME = {"blur", "crop", "pad"}
VALID_MODE = {"auto", "manual"}
CLIP_ID_RE = re.compile(r"^[a-f0-9]{12}-\d+$")


class JobRequest(BaseModel):
    url: str
    mode: str = "auto"
    reframe: str = "blur"
    captions: bool = True
    highlight: bool = True
    language: Optional[str] = None
    # Manual mode:
    start: Optional[str] = None
    end: Optional[str] = None
    # Auto mode:
    num_clips: int = 3


@app.get("/api/health")
def health():
    """Report whether required binaries and the Claude API key are present."""
    return {
        "ffmpeg": have_binary("ffmpeg"),
        "ffprobe": have_binary("ffprobe"),
        "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }


@app.post("/api/jobs")
def create_job(req: JobRequest):
    if not req.url.strip():
        raise HTTPException(400, "A video URL is required.")
    if req.mode not in VALID_MODE:
        raise HTTPException(400, f"mode must be one of {sorted(VALID_MODE)}.")
    if req.reframe not in VALID_REFRAME:
        raise HTTPException(400, f"reframe must be one of {sorted(VALID_REFRAME)}.")

    fields = dict(
        url=req.url.strip(),
        mode=req.mode,
        reframe=req.reframe,
        captions=req.captions,
        highlight=req.highlight,
        language=(req.language or None),
    )

    if req.mode == "manual":
        try:
            start = parse_timestamp(req.start)
            end = parse_timestamp(req.end)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if start is not None and end is not None:
            if end <= start:
                raise HTTPException(400, "End time must be after start time.")
            if end - start > MAX_CLIP_SECONDS:
                raise HTTPException(400, f"Clip is too long (max {MAX_CLIP_SECONDS}s).")
        fields.update(start=start, end=end)
    else:
        fields.update(
            num_clips=max(1, min(AUTO_MAX_CLIPS, req.num_clips)),
            min_len=AUTO_MIN_LEN,
            max_len=AUTO_MAX_LEN,
        )

    job = store.create(**fields)
    start_job(job)
    return {"id": job.id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return store.to_dict(job)


@app.get("/api/clips/{clip_id}")
def clip_file(clip_id: str):
    """Serve a rendered clip for preview/download (supports range requests)."""
    if not CLIP_ID_RE.match(clip_id):
        raise HTTPException(400, "Invalid clip id.")
    path = OUTPUT_DIR / f"{clip_id}.mp4"
    if not path.exists():
        raise HTTPException(404, "Clip not found.")
    return FileResponse(path, media_type="video/mp4", filename=f"short-{clip_id}.mp4")


# Serve the frontend at the root. Mounted last so /api/* routes take priority.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
