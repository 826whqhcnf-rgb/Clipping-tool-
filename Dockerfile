FROM python:3.11-slim

# ffmpeg does all rendering; libglib is needed by opencv-python-headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

# Hosts like HF Spaces run containers as an arbitrary user, so keep all
# writable state (jobs, model cache) under /tmp.
ENV CLIP_DATA_DIR=/tmp/clip-data \
    HF_HOME=/tmp/hf-cache \
    XDG_CACHE_HOME=/tmp/cache

# Pre-download the tiny Whisper model at build time (best-effort) so the first
# transcription doesn't stall on a cold download.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', device='cpu', compute_type='int8')" || true

# 7860 is Hugging Face Spaces' default app port; Render/Railway inject PORT.
EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
