# 🎬 Shorts Clipper

Turn any video into ready-to-post, captioned vertical **YouTube Shorts** — a
self-hosted, Viblo-style clipping tool.

Paste a video URL and the tool will:

1. **Download** it (via `yt-dlp`)
2. **Transcribe** the speech (via `faster-whisper`, with word-level timing)
3. **Find the best moments automatically** — Claude reads the transcript and
   picks the most viral, self-contained clips, each with a **title**, a
   **virality score**, and a one-line reason (this is the "Auto" mode). Or pick
   an exact time range yourself in "Manual" mode.
4. **Reframe to vertical 9:16** (1080×1920) — blurred background, center crop,
   or letterbox.
5. **Burn animated captions** — karaoke-style word highlighting, the popular
   Shorts/Reels/TikTok look.
6. Let you **preview and download** each finished Short in the browser.

> ⚖️ Only clip content you own or have permission to use, and respect each
> platform's Terms of Service and copyright.

---

## Quick start

### 1. Prerequisites

- **Python 3.10+**
- **ffmpeg** (and `ffprobe`) on your `PATH`:
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: [download from ffmpeg.org](https://ffmpeg.org/download.html)

### 2. (Optional but recommended) Enable smart clip picking

Auto mode is best with Claude. Set your Anthropic API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Without a key, Auto mode still works using a speech-density heuristic — it just
won't be as good at finding the genuinely shareable moments.

### 3. Run it

```bash
./run.sh
```

Then open **http://localhost:8000**.

(`run.sh` creates a virtualenv and installs dependencies on first run. To do it
manually: `pip install -r requirements.txt` then
`uvicorn backend.main:app --reload`.)

---

## How it works

```
URL ─▶ yt-dlp ─▶ extract audio ─▶ faster-whisper (word timings)
                                        │
                    ┌───────────────────┴───────────────────┐
              Auto mode                                 Manual mode
   Claude picks N viral moments              your chosen start/end range
   (title + score + reason)
                    └───────────────────┬───────────────────┘
                                        ▼
              ffmpeg: trim → reframe 9:16 → burn .ass captions
                                        ▼
                            MP4 Shorts you can download
```

### Project layout

```
backend/
  main.py              FastAPI app + REST endpoints + static frontend
  config.py            Paths and settings (all overridable via env vars)
  jobs.py              In-memory job store + background worker threads
  utils.py             Timestamp parsing, ffmpeg/ffprobe helpers
  pipeline/
    download.py        yt-dlp wrapper
    transcribe.py      faster-whisper word-level transcription
    highlights.py      Claude-powered (or heuristic) moment detection
    reframe.py         9:16 ffmpeg filters (blur / crop / pad)
    captions.py        Transcript → styled .ass subtitles
    render.py          Trim + reframe + burn captions for one clip
    process.py         Orchestrates the whole pipeline per job
frontend/
  index.html / styles.css / app.js   Single-page UI (no build step)
run.sh                 One-command launcher
```

---

## Configuration

All settings have sensible defaults; override with environment variables.

Auto mode picks an AI provider by whichever key is set, in this order:
**Gemini → Claude → offline heuristic**.

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | Enables Google Gemini highlight detection (free tier — get one at [aistudio.google.com](https://aistudio.google.com/apikey)) |
| `CLIP_GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model used to pick highlights |
| `ANTHROPIC_API_KEY` | — | Enables Claude highlight detection (used if no Gemini key) |
| `CLIP_HIGHLIGHT_MODEL` | `claude-opus-4-8` | Claude model used to pick highlights |
| `CLIP_WHISPER_MODEL` | `base` | Whisper size: `tiny`/`base`/`small`/`medium`/`large-v3` |
| `CLIP_WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` |
| `CLIP_WHISPER_COMPUTE` | `int8` | `int8` (CPU) / `float16` (GPU) / `float32` |
| `CLIP_DATA_DIR` | `./data` | Where downloads, work files, and outputs live |
| `CLIP_OUTPUT_W` / `CLIP_OUTPUT_H` | `1080` / `1920` | Output dimensions |
| `CLIP_AUTO_MIN_LEN` / `CLIP_AUTO_MAX_LEN` | `15` / `60` | Auto-clip length bounds (seconds) |
| `CLIP_AUTO_MAX_CLIPS` | `10` | Max clips Auto mode will produce |
| `CLIP_MAX_SECONDS` | `180` | Max length for a single Manual clip |
| `CLIP_COOKIES_FILE` | — | Path to a `cookies.txt` for sites that need login / a bot check (see Troubleshooting) |
| `PORT` | `8000` | Server port (read by `run.sh`) |

---

## Troubleshooting

**YouTube: "Sign in to confirm you're not a bot."**
YouTube blocks downloads from cloud/datacenter IPs (Codespaces, most VPS
hosts). The tool already tries the player clients that usually slip past this;
if you still hit it, either:

- **Run on a home/residential network** (a laptop at home rarely gets blocked), or
- **Supply cookies.** Export a `cookies.txt` from a browser logged into YouTube
  (e.g. the "Get cookies.txt LOCALLY" extension on a desktop), upload it to the
  machine, and set `export CLIP_COOKIES_FILE=/path/to/cookies.txt` before
  starting the server.

> **Speed note:** transcription runs on CPU by default. For long videos, use a
> smaller Whisper model (`tiny`/`base`) or a CUDA GPU
> (`CLIP_WHISPER_DEVICE=cuda CLIP_WHISPER_COMPUTE=float16`).

---

## API

The frontend is just a client of a small REST API:

- `POST /api/jobs` — start a job. Body: `{ url, mode, reframe, captions,
  highlight, language, num_clips?, start?, end? }`. Returns `{ id }`.
- `GET /api/jobs/{id}` — poll status, progress, and finished `clips[]`.
- `GET /api/clips/{clip_id}` — stream/download a rendered clip (supports HTTP
  range requests, so the in-page player can seek).

---

## Roadmap ideas

- Speaker/face tracking for smarter reframing
- Per-clip caption style presets (fonts, colors, position)
- A queue + persistent storage for multi-user use
- Direct upload to YouTube via the Data API
