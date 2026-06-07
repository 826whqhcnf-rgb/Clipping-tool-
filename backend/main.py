"""FastAPI app: REST endpoints + static frontend for the Shorts Clipper."""
from __future__ import annotations

import os
import re
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    AUTO_MAX_CLIPS,
    AUTO_MAX_LEN,
    AUTO_MIN_LEN,
    DOWNLOAD_DIR,
    FRONTEND_DIR,
    MAX_CLIP_SECONDS,
    MAX_UPLOAD_MB,
    OUTPUT_DIR,
)
from .jobs import start_job, store
from .pipeline.captions import PRESETS
from .utils import have_binary, parse_timestamp

app = FastAPI(title="Shorts Clipper")

VALID_REFRAME = {"blur", "crop", "pad"}
VALID_MODE = {"auto", "manual"}
VALID_STYLE = set(PRESETS)
CLIP_ID_RE = re.compile(r"^[a-f0-9]{12}-\d+$")
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


class JobRequest(BaseModel):
    url: str
    mode: str = "auto"
    reframe: str = "blur"
    captions: bool = True
    highlight: bool = True
    caption_style: str = "karaoke"
    language: Optional[str] = None
    # Manual mode:
    start: Optional[str] = None
    end: Optional[str] = None
    # Auto mode:
    num_clips: int = 3


@app.get("/api/health")
def health():
    """Report binary availability and which AI provider will pick highlights."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        provider = "gemini"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        provider = "claude"
    else:
        provider = "heuristic"
    return {
        "ffmpeg": have_binary("ffmpeg"),
        "ffprobe": have_binary("ffprobe"),
        "provider": provider,
    }


def _build_job_fields(
    *, url, mode, reframe, captions, highlight, caption_style, language, start, end, num_clips
) -> dict:
    """Validate shared inputs and build the kwargs for store.create()."""
    if mode not in VALID_MODE:
        raise HTTPException(400, f"mode must be one of {sorted(VALID_MODE)}.")
    if reframe not in VALID_REFRAME:
        raise HTTPException(400, f"reframe must be one of {sorted(VALID_REFRAME)}.")
    if caption_style not in VALID_STYLE:
        raise HTTPException(400, f"caption_style must be one of {sorted(VALID_STYLE)}.")

    fields = dict(
        url=url,
        mode=mode,
        reframe=reframe,
        captions=captions,
        highlight=highlight,
        caption_style=caption_style,
        language=(language or None),
    )

    if mode == "manual":
        try:
            start_s = parse_timestamp(start)
            end_s = parse_timestamp(end)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if start_s is not None and end_s is not None:
            if end_s <= start_s:
                raise HTTPException(400, "End time must be after start time.")
            if end_s - start_s > MAX_CLIP_SECONDS:
                raise HTTPException(400, f"Clip is too long (max {MAX_CLIP_SECONDS}s).")
        fields.update(start=start_s, end=end_s)
    else:
        fields.update(
            num_clips=max(1, min(AUTO_MAX_CLIPS, num_clips)),
            min_len=AUTO_MIN_LEN,
            max_len=AUTO_MAX_LEN,
        )
    return fields


@app.post("/api/jobs")
def create_job(req: JobRequest):
    """Start a job from a video URL (downloaded via yt-dlp)."""
    if not req.url.strip():
        raise HTTPException(400, "A video URL is required.")
    fields = _build_job_fields(
        url=req.url.strip(), mode=req.mode, reframe=req.reframe,
        captions=req.captions, highlight=req.highlight, caption_style=req.caption_style,
        language=req.language, start=req.start, end=req.end, num_clips=req.num_clips,
    )
    job = store.create(**fields)
    start_job(job)
    return {"id": job.id}


class UploadInit(BaseModel):
    filename: str = "video.mp4"
    mode: str = "auto"
    reframe: str = "blur"
    captions: bool = True
    highlight: bool = True
    caption_style: str = "karaoke"
    language: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    num_clips: int = 3


class UploadComplete(BaseModel):
    filename: str = "video.mp4"


def _part_path(job_id: str) -> Path:
    return DOWNLOAD_DIR / f"{job_id}.part"


@app.post("/api/uploads/init")
def upload_init(req: UploadInit):
    """Begin a chunked upload: create the job (not started) and return its id.

    Files are uploaded in small chunks to stay under proxy/body-size limits
    (e.g. the GitHub Codespaces port-forwarding cap), then reassembled here.
    """
    fields = _build_job_fields(
        url="", mode=req.mode, reframe=req.reframe, captions=req.captions,
        highlight=req.highlight, caption_style=req.caption_style,
        language=req.language, start=req.start, end=req.end, num_clips=req.num_clips,
    )
    job = store.create(**fields)
    job.status = "uploading"
    job.message = "Uploading…"
    _part_path(job.id).unlink(missing_ok=True)  # start fresh
    return {"id": job.id}


@app.post("/api/uploads/{job_id}/chunk")
async def upload_chunk(job_id: str, request: Request):
    """Append one binary chunk to the in-progress upload."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(400, "Invalid upload id.")
    job = store.get(job_id)
    if not job or job.status != "uploading":
        raise HTTPException(404, "Upload session not found.")

    part = _part_path(job_id)
    data = await request.body()
    with part.open("ab") as out:
        out.write(data)

    if part.stat().st_size > MAX_UPLOAD_BYTES:
        part.unlink(missing_ok=True)
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_MB} MB limit.")
    return {"received": part.stat().st_size}


@app.post("/api/uploads/{job_id}/complete")
def upload_complete(job_id: str, req: UploadComplete):
    """Finalize the upload and start processing."""
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(400, "Invalid upload id.")
    job = store.get(job_id)
    if not job or job.status != "uploading":
        raise HTTPException(404, "Upload session not found.")

    part = _part_path(job_id)
    if not part.exists() or part.stat().st_size == 0:
        raise HTTPException(400, "No data was uploaded.")

    suffix = Path(req.filename).suffix.lower() or ".mp4"
    dest = DOWNLOAD_DIR / f"{job_id}{suffix}"
    part.replace(dest)

    job.source_path = str(dest)
    job.title = Path(req.filename).stem or "upload"
    job.status = "queued"
    job.message = "Queued"
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
