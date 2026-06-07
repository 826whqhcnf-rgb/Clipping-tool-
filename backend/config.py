"""Central configuration and on-disk paths for the Shorts Clipper app.

All values can be overridden with environment variables so the same code runs
on a laptop or a small server without edits.
"""
from __future__ import annotations

import os
from pathlib import Path

# Project root (the directory that contains `backend/` and `frontend/`).
BASE_DIR = Path(__file__).resolve().parent.parent

# Where downloads, intermediate files and finished clips live.
DATA_DIR = Path(os.environ.get("CLIP_DATA_DIR", BASE_DIR / "data"))
DOWNLOAD_DIR = DATA_DIR / "downloads"
OUTPUT_DIR = DATA_DIR / "outputs"
WORK_DIR = DATA_DIR / "work"

for _d in (DOWNLOAD_DIR, OUTPUT_DIR, WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Frontend assets.
FRONTEND_DIR = BASE_DIR / "frontend"

# --- Whisper (faster-whisper) settings ---------------------------------------
# Model size:   tiny | base | small | medium | large-v3
#   "base" is a good speed/quality tradeoff on CPU. Use "small"/"medium" for
#   better accuracy if you have the time or a GPU.
WHISPER_MODEL = os.environ.get("CLIP_WHISPER_MODEL", "base")
# Device:       cpu | cuda
WHISPER_DEVICE = os.environ.get("CLIP_WHISPER_DEVICE", "cpu")
# Compute type: int8 (fast, CPU) | float16 (GPU) | float32
WHISPER_COMPUTE = os.environ.get("CLIP_WHISPER_COMPUTE", "int8")

# --- Auto-highlight detection (Claude) ---------------------------------------
# Used only in "auto" mode, and only when ANTHROPIC_API_KEY is set. Without a
# key, the tool falls back to a speech-density heuristic.
HIGHLIGHT_MODEL = os.environ.get("CLIP_HIGHLIGHT_MODEL", "claude-opus-4-8")

# --- Output video dimensions (9:16 vertical) ---------------------------------
OUTPUT_W = int(os.environ.get("CLIP_OUTPUT_W", 1080))
OUTPUT_H = int(os.environ.get("CLIP_OUTPUT_H", 1920))

# Cap a single manual clip so a typo can't kick off an hour-long render. Seconds.
MAX_CLIP_SECONDS = int(os.environ.get("CLIP_MAX_SECONDS", 180))

# Bounds for auto-mode clip lengths (seconds) and how many clips to request.
AUTO_MIN_LEN = float(os.environ.get("CLIP_AUTO_MIN_LEN", 15))
AUTO_MAX_LEN = float(os.environ.get("CLIP_AUTO_MAX_LEN", 60))
AUTO_MAX_CLIPS = int(os.environ.get("CLIP_AUTO_MAX_CLIPS", 10))
