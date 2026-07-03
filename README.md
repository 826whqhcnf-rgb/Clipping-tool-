# 🎬 Shorts Clipper

Turn any video into ready-to-post, captioned vertical **YouTube Shorts** — a
self-hosted, Viblo-style clipping tool.

Paste a video URL and the tool will:

1. **Download** it (via `yt-dlp`)
2. **Transcribe** the speech (via `faster-whisper`, with word-level timing)
3. **Find the best moments automatically** — an LLM (free Gemini, or Claude)
   reads the transcript and picks the most viral, self-contained clips, each
   with a **title**, a **virality score**, a reason, and ready-to-post
   **hashtags** (this is "Auto" mode). Or pick an exact time range in "Manual"
   mode (which only transcribes the segment you chose, so it's fast).
4. **Reframe to vertical 9:16** (1080×1920) — blurred background, **smart crop
   that keeps the speaker centered** (OpenCV face detection, offline), plain
   center crop, or letterbox — and **normalize loudness** so every clip plays
   at a consistent volume.
5. **Burn animated captions** — karaoke word-pop highlighting in selectable
   styles (karaoke / boxed / clean), the popular Shorts/Reels/TikTok look.
6. **Preview, copy a caption (title + hashtags), and download** each finished
   Short — or **post it straight to YouTube** (optional, see below).

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

Auto mode and Generate mode are best with an AI key. The free Gemini tier is
the easiest ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)):

```bash
export GEMINI_API_KEY=AIza...
```

(`ANTHROPIC_API_KEY` works too.) Without a key, Auto mode still works using a
speech-density heuristic — it just won't be as good at finding the genuinely
shareable moments, and Generate mode needs a key for scripts.

### 3. Run it

```bash
./run.sh
```

Then open **http://localhost:8000**.

(`run.sh` creates a virtualenv and installs dependencies on first run. To do it
manually: `pip install -r requirements.txt` then
`uvicorn backend.main:app --reload`.)

### Run it in the browser (GitHub Codespaces)

No local install needed — good for iPad/Chromebook. On GitHub: **`<> Code` →
Codespaces → Create codespace**. The included devcontainer installs `ffmpeg` +
dependencies and **auto-starts the server**, then opens port 8000. To use the
free Gemini picker, add a Codespaces **secret** named `GEMINI_API_KEY`
(Settings → Codespaces → Secrets) so it's present on every launch.

After a `git pull`, reload new code with `bash scripts/restart.sh`.

**Getting a big video in (most reliable):** browser uploads go through the
Codespaces proxy, which caps request size. The tool uploads in chunks to cope,
but the surest route is the **"On server"** source: in the VS Code Explorer,
upload your video into `data/input/`, then pick it from the list in the app —
no HTTP upload at all.

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
    highlights.py      LLM (Gemini/Claude) or heuristic moment detection
    reframe.py         9:16 ffmpeg filters (blur / face / crop / pad)
    facecrop.py        OpenCV speaker detection for the smart crop
    captions.py        Transcript → styled .ass subtitles (+ banner/watermark)
    render.py          Trim + reframe + burn captions for one clip
    generate.py        Original narrated Shorts (scripts + TTS + compose)
    tts.py             Free Edge text-to-speech with word timings
    broll.py           Free Pexels stock-footage backgrounds
    process.py         Orchestrates the whole pipeline per job
  youtube.py           OAuth device flow + YouTube upload
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

- `POST /api/jobs` — start a job from a URL. Body: `{ url, mode, reframe,
  captions, highlight, language, num_clips?, start?, end? }`. Returns `{ id }`.
- `POST /api/uploads/init` → `POST /api/uploads/{id}/chunk` (raw bytes,
  repeated) → `POST /api/uploads/{id}/complete` — chunked file upload that
  stays under proxy body-size limits. Skips downloading entirely.
- `GET /api/files` + `server_file` on `/api/jobs` — clip a file already in
  `data/input/`. `POST /api/generate` — original narrated Shorts from a topic.
- `GET /api/jobs/{id}/zip` — download all of a job's clips. `/api/youtube/*` —
  connect + post clips. `GET /api/voices` — TTS voices for Generate mode.
- `GET /api/jobs/{id}` — poll status, progress, and finished `clips[]`.
- `GET /api/clips/{clip_id}` — stream/download a rendered clip (supports HTTP
  range requests, so the in-page player can seek).

---

## Generate original Shorts (no source video)

The **✨ Generate** tab makes original narrated Shorts from a topic — ideal for
faceless niches (e.g. finance education) that you fully own and can monetize.

Pipeline: an LLM writes a punchy, virality-optimized script → free Microsoft
Edge TTS voices it (no API key, and it gives word timings) → animated captions
over a clean background → a 9:16 MP4. The results are normal clips, so
**Download / Post to YouTube / Post all** work on them unchanged.

Each generated Short gets a **gradient background, a top title banner, animated
captions, and an accent progress bar** (the retention look finance channels use).

- Needs an AI key for the scripts (`GEMINI_API_KEY` free, or `ANTHROPIC_API_KEY`).
  The voice is free.
- **Voice:** pick one in the UI (7 free Edge voices), or set `CLIP_TTS_VOICE` /
  `CLIP_TTS_RATE` (default `en-US-GuyNeural`, `+8%`).
- **Background music (optional):** drop a royalty-free track into `data/music/`
  (or set `CLIP_MUSIC=/path/to.mp3`) and it's mixed in, ducked under the voice
  (`CLIP_MUSIC_VOLUME`, default `0.10`). Free tracks: YouTube Studio Audio
  Library, Pixabay Music, or Free Music Archive — check each track's licence.
- **Stock B-roll (optional, big quality boost):** set a free `PEXELS_API_KEY`
  ([pexels.com/api](https://www.pexels.com/api/)) and each Short plays relevant
  stock footage (markets, money, city) dimmed behind the captions. Falls back
  to the gradient when there's no key or no match.
- **Colours:** `CLIP_GEN_BG_TOP` / `CLIP_GEN_BG_BOTTOM` (gradient) and
  `CLIP_GEN_BAR` (progress bar).
- **Virality target:** scripts are regenerated until they hit `CLIP_VIRALITY_TARGET`
  (default 90) when possible; the score is the model's own estimate, not a
  guarantee of real-world performance.

## Posting to YouTube (optional)

You can publish a finished Short straight from the app. It uses Google's OAuth
**device flow**, so it works on Codespaces with no redirect-URL setup.

**One-time setup:**

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a
   project and **enable the "YouTube Data API v3"**.
2. Configure the OAuth consent screen (External; add yourself as a test user).
3. Create an **OAuth client ID** of type **"TVs and Limited Input devices"**.
4. Set the client id/secret as env vars (or Codespaces secrets):
   ```bash
   export YOUTUBE_CLIENT_ID=...apps.googleusercontent.com
   export YOUTUBE_CLIENT_SECRET=...
   ```
   Restart the server (`bash scripts/restart.sh`).

**Use it:** after clips are generated, the results area shows a **Connect
YouTube** button → open the shown URL on any device, enter the code, done. Then
each clip gets a **▶️ Post to YouTube** button, or use **Post all to YouTube**
to publish the whole batch (pick Unlisted/Public/Private).

**Clipping campaigns:** the form has a **Campaign tags / mention** field — put
the hashtags / @handle / link a campaign requires (e.g. `#cod #mw4 @brand`) and
they're stamped onto every clip's caption and YouTube description automatically.
There's also a **Watermark** field: whatever you type (e.g. the campaign's
required `@creatorhandle`) is burned onto the video itself, bottom-right, on
every clip. Clips in a batch render **in parallel** (`CLIP_RENDER_WORKERS`,
default = CPU count capped at 2), and auto-mode clip boundaries snap to word
starts so clips never open mid-word.

**Heads-up / limits:**
- The YouTube API gives ~**6 uploads/day** by default (each upload costs 1600 of
  the 10,000 daily quota units). Request more in the Cloud Console if needed.
- While your OAuth app is in "testing", authorizations expire after 7 days —
  just reconnect.
- **You are responsible for what you publish.** Only post content you own or
  have the rights to; uploading others' videos can get your channel struck.
  Default visibility is **Unlisted** so you can review before going public.

## Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite covers the pure logic (timestamp parsing, caption generation +
escaping, word grouping/slicing, reframe filters, and highlight
clamping/deduplication) — no ffmpeg or network needed.

## Roadmap ideas

- Speaker/face tracking for smarter reframing
- Per-clip caption style presets (fonts, colors, position)
- A queue + persistent storage for multi-user use
- Direct upload to YouTube via the Data API
