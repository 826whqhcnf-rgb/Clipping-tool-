"""FastAPI app: REST endpoints + static frontend for the Shorts Clipper."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Optional

from pathlib import Path

import io
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    AUTO_MAX_CLIPS,
    AUTO_MAX_LEN,
    AUTO_MIN_LEN,
    DOWNLOAD_DIR,
    FRONTEND_DIR,
    INPUT_DIR,
    MAX_CLIP_SECONDS,
    MAX_UPLOAD_MB,
    OUTPUT_DIR,
)
from .jobs import start_job, store
from .pipeline.captions import PRESETS
from . import youtube as yt
from .utils import have_binary, parse_timestamp

app = FastAPI(title="Shorts Clipper")


@app.middleware("http")
async def no_store_frontend(request: Request, call_next):
    """Stop browsers caching the HTML/JS/CSS, so updates always show up."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


# --- Optional password gate (set CLIP_PASSWORD when hosting publicly) ---------
AUTH_COOKIE = "clip_auth"


def _auth_token() -> str:
    """Cookie value that proves the password was entered (single-user tool)."""
    return hashlib.sha256(f"clip:{os.environ['CLIP_PASSWORD']}".encode()).hexdigest()


_LOGIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shorts Clipper — Sign in</title>
<style>body{font-family:-apple-system,sans-serif;background:#0f1117;color:#e8eaf0;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
form{background:#181b25;border:1px solid #2b303d;border-radius:14px;padding:28px;
width:min(90vw,340px)}h1{font-size:1.2rem;margin:0 0 14px}input{width:100%;
box-sizing:border-box;padding:11px;background:#20242f;border:1px solid #2b303d;
border-radius:10px;color:#e8eaf0;font-size:1rem;margin-bottom:12px}
button{width:100%;padding:12px;border:0;border-radius:10px;background:#6c5ce7;
color:#fff;font-weight:700;font-size:1rem;cursor:pointer}
p{color:#ff6b6b;font-size:.85rem;min-height:1em;margin:10px 0 0}</style></head>
<body><form id="f"><h1>🎬 Shorts Clipper</h1>
<input type="password" id="p" placeholder="Password" autofocus>
<button>Sign in</button><p id="e"></p></form>
<script>document.getElementById("f").onsubmit=async(ev)=>{ev.preventDefault();
const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({password:document.getElementById("p").value})});
if(r.ok){location.href="/";}else{document.getElementById("e").textContent="Wrong password";}};
</script></body></html>"""


@app.middleware("http")
async def password_gate(request: Request, call_next):
    """Require the CLIP_PASSWORD cookie for everything except the login flow.

    No-op when CLIP_PASSWORD isn't set (local/private use stays friction-free).
    """
    if os.environ.get("CLIP_PASSWORD"):
        path = request.url.path
        if path not in ("/login", "/api/login"):
            cookie = request.cookies.get(AUTH_COOKIE, "")
            if not hmac.compare_digest(cookie, _auth_token()):
                if path.startswith("/api"):
                    return JSONResponse({"detail": "Not signed in."}, status_code=401)
                return RedirectResponse("/login", status_code=302)
    return await call_next(request)


@app.get("/login")
def login_page():
    return HTMLResponse(_LOGIN_PAGE)


class LoginRequest(BaseModel):
    password: str = ""


@app.post("/api/login")
def login(req: LoginRequest):
    expected = os.environ.get("CLIP_PASSWORD")
    if not expected:
        return {"ok": True}
    if not hmac.compare_digest(req.password, expected):
        raise HTTPException(401, "Wrong password.")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(AUTH_COOKIE, _auth_token(), httponly=True, samesite="lax",
                    max_age=60 * 60 * 24 * 90)
    return resp

VALID_REFRAME = {"blur", "crop", "pad", "face"}
VALID_MODE = {"auto", "manual"}
VALID_STYLE = set(PRESETS)
CLIP_ID_RE = re.compile(r"^[a-f0-9]{12}-\d+$")
JOB_ID_RE = re.compile(r"^[a-f0-9]{12}$")
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


class JobRequest(BaseModel):
    url: str = ""
    mode: str = "auto"
    reframe: str = "blur"
    captions: bool = True
    highlight: bool = True
    caption_style: str = "karaoke"
    watermark: str = ""
    language: Optional[str] = None
    # Source: provide either a URL or a file already on the server (in INPUT_DIR).
    server_file: Optional[str] = None
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
    *, url, mode, reframe, captions, highlight, caption_style, language, start, end,
    num_clips, watermark=""
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
        watermark=(watermark or "").strip()[:60],
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


def _resolve_input_file(name: str) -> Path:
    """Safely resolve a filename to a file inside INPUT_DIR (no path traversal)."""
    candidate = (INPUT_DIR / Path(name).name).resolve()
    if candidate.parent != INPUT_DIR.resolve() or not candidate.is_file():
        raise HTTPException(400, "That file was not found in the input folder.")
    return candidate


@app.get("/api/files")
def list_input_files():
    """List video files available in INPUT_DIR (drop files there to clip them)."""
    files = []
    for p in sorted(INPUT_DIR.glob("*")):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            files.append({"name": p.name, "size_mb": round(p.stat().st_size / 1048576, 1)})
    return {"files": files, "dir": str(INPUT_DIR)}


@app.post("/api/jobs")
def create_job(req: JobRequest):
    """Start a job from a video URL or a file already on the server."""
    server_path = _resolve_input_file(req.server_file) if req.server_file else None
    if not server_path and not req.url.strip():
        raise HTTPException(400, "Provide a video URL or pick a file on the server.")

    fields = _build_job_fields(
        url="" if server_path else req.url.strip(), mode=req.mode, reframe=req.reframe,
        captions=req.captions, highlight=req.highlight, caption_style=req.caption_style,
        language=req.language, start=req.start, end=req.end, num_clips=req.num_clips,
        watermark=req.watermark,
    )
    job = store.create(**fields)
    if server_path:
        job.source_path = str(server_path)
        job.title = server_path.stem
    start_job(job)
    return {"id": job.id}


class UploadInit(BaseModel):
    filename: str = "video.mp4"
    mode: str = "auto"
    reframe: str = "blur"
    captions: bool = True
    highlight: bool = True
    caption_style: str = "karaoke"
    watermark: str = ""
    language: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    num_clips: int = 3


class UploadComplete(BaseModel):
    filename: str = "video.mp4"


def _part_path(job_id: str) -> Path:
    return DOWNLOAD_DIR / f"{job_id}.part"


class GenerateRequest(BaseModel):
    topic: str = "finance concepts and recent financial events"
    num_clips: int = 3
    caption_style: str = "karaoke"
    voice: Optional[str] = None
    language: Optional[str] = None


@app.get("/api/voices")
def list_voices():
    """Narration voices available for Generate mode."""
    from .pipeline.tts import VOICES
    return {"voices": [{"id": v, "label": label} for v, label in VOICES]}


@app.post("/api/generate")
def create_generate_job(req: GenerateRequest):
    """Generate original narrated Shorts from a topic (no source video)."""
    if req.caption_style not in VALID_STYLE:
        raise HTTPException(400, f"caption_style must be one of {sorted(VALID_STYLE)}.")
    job = store.create(
        url="", mode="generate", reframe="blur", captions=True, highlight=True,
        caption_style=req.caption_style, language=(req.language or None),
        num_clips=max(1, min(AUTO_MAX_CLIPS, req.num_clips)),
        min_len=AUTO_MIN_LEN, max_len=AUTO_MAX_LEN,
    )
    job.topic = req.topic.strip() or "finance concepts and recent financial events"
    job.voice = req.voice
    start_job(job)
    return {"id": job.id}


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
        watermark=req.watermark,
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


@app.get("/api/jobs/{job_id}/zip")
def download_all(job_id: str):
    """Bundle all of a job's finished clips into a single zip for download."""
    job = store.get(job_id)
    if not job or not job.clips:
        raise HTTPException(404, "No clips to download.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # mp4 is already compressed
        for clip in job.clips:
            path = OUTPUT_DIR / clip["output"]
            if path.exists():
                zf.write(path, arcname=f"short-{clip['index']:02d}.mp4")
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="shorts-{job_id}.zip"'},
    )


class YouTubeUpload(BaseModel):
    clip_id: str
    title: str = "Short"
    description: str = ""
    tags: list[str] = []
    privacy: str = "unlisted"


@app.get("/api/youtube/status")
def youtube_status():
    """Report whether posting is set up and whether the user has authorized."""
    return {"configured": yt.is_configured(), "connected": yt.is_connected()}


@app.post("/api/youtube/connect")
def youtube_connect():
    """Begin the device-code flow; returns a code + URL for the user to enter."""
    if not yt.is_configured():
        raise HTTPException(400, "YouTube posting isn't set up (see the README).")
    try:
        return yt.start_device_flow()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't start Google authorization: {exc}")


@app.post("/api/youtube/poll")
def youtube_poll():
    """Poll once for authorization completion."""
    return {"status": yt.poll_device()}


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    yt.disconnect()
    return {"connected": False}


@app.post("/api/youtube/upload")
def youtube_upload(req: YouTubeUpload):
    """Upload a finished clip to the connected YouTube account."""
    if not yt.is_connected():
        raise HTTPException(400, "Connect a YouTube account first.")
    if not CLIP_ID_RE.match(req.clip_id):
        raise HTTPException(400, "Invalid clip id.")
    path = OUTPUT_DIR / f"{req.clip_id}.mp4"
    if not path.exists():
        raise HTTPException(404, "Clip not found.")
    try:
        video_id = yt.upload(path, req.title, req.description, req.tags, req.privacy)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Upload failed: {str(exc)[:300]}")
    return {"video_id": video_id, "url": f"https://youtu.be/{video_id}"}


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
