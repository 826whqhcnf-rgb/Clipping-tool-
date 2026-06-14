"""Free text-to-speech via Microsoft Edge voices (no API key required).

Returns the audio bytes plus word-level timings (from edge-tts WordBoundary
events), so captions line up with the narration without needing Whisper.
"""
from __future__ import annotations

import asyncio
import os
from typing import List, Tuple

from .transcribe import Word

# A clear, neutral narration voice; override with CLIP_TTS_VOICE.
DEFAULT_VOICE = os.environ.get("CLIP_TTS_VOICE", "en-US-GuyNeural")
# Slightly brisk delivery reads as more energetic on Shorts; override with CLIP_TTS_RATE.
DEFAULT_RATE = os.environ.get("CLIP_TTS_RATE", "+8%")

# Voices offered in the UI (all free Edge Neural voices).
VOICES = [
    ("en-US-GuyNeural", "Guy — confident (US, m)"),
    ("en-US-AndrewNeural", "Andrew — warm (US, m)"),
    ("en-US-AriaNeural", "Aria — clear (US, f)"),
    ("en-US-JennyNeural", "Jenny — friendly (US, f)"),
    ("en-GB-RyanNeural", "Ryan — British (UK, m)"),
    ("en-GB-SoniaNeural", "Sonia — British (UK, f)"),
    ("en-AU-WilliamNeural", "William — Australian (m)"),
]
_VOICE_IDS = {v for v, _ in VOICES}


def synthesize(text: str, voice: str = DEFAULT_VOICE, rate: str = DEFAULT_RATE) -> Tuple[bytes, List[Word]]:
    """Synthesize `text`. Returns (mp3_bytes, words) with clip-relative timings."""
    import edge_tts

    if voice not in _VOICE_IDS:
        voice = DEFAULT_VOICE

    words: List[Word] = []
    chunks: List[bytes] = []

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        async for ch in communicate.stream():
            if ch["type"] == "audio":
                chunks.append(ch["data"])
            elif ch["type"] == "WordBoundary":
                start = ch["offset"] / 1e7          # 100-ns units -> seconds
                dur = ch["duration"] / 1e7
                token = (ch.get("text") or "").strip()
                if token:
                    words.append({"start": start, "end": start + dur, "text": token})

    asyncio.run(_run())
    if not chunks:
        raise RuntimeError("Text-to-speech produced no audio (check network access).")
    return b"".join(chunks), words
