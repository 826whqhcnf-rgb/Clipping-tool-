"""Generate original narrated Shorts from a topic (e.g. finance education).

Pipeline: LLM writes a punchy script -> free TTS voices it (with word timings)
-> animated captions -> ffmpeg composes a 9:16 video over a solid background.
Output clips match the normal job/clip shape, so preview / download / YouTube
posting all work unchanged.

Needs an AI key (Gemini or Claude) for the scripts; the voice is free.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

from ..config import GEMINI_MODEL, HIGHLIGHT_MODEL, OUTPUT_DIR, WORK_DIR
from ..utils import ffprobe_duration, run
from .captions import build_ass
from .tts import synthesize

BACKGROUND = os.environ.get("CLIP_GEN_BG", "0x0B1F3A")  # deep navy — finance vibe


@dataclass
class Script:
    title: str
    hook: str
    script: str
    score: int
    hashtags: List[str]


SYSTEM_PROMPT = (
    "You are a viral short-form finance educator who writes YouTube Shorts that "
    "explain finance concepts, events, and history simply and unforgettably.\n"
    "Write each script to MAXIMIZE virality (target a 90+ score) using:\n"
    "- A scroll-stopping hook in the first sentence (a bold claim, a surprising "
    "number, or a question that opens a curiosity gap).\n"
    "- A clear, fast explanation with one concrete, specific detail or number.\n"
    "- A memorable one-line payoff at the end.\n"
    "Rules: spoken narration only (no stage directions, no emojis, no headings), "
    "roughly 110-150 words (~35-45s), plain language a beginner understands. The "
    "'script' field is the full narration to read aloud, and it must START with the hook.\n"
    "title: <=60 chars, curiosity-driven, no surrounding quotes.\n"
    "hashtags: 4-6 lowercase finance-relevant tags, no # symbol.\n"
    "score: your honest 0-100 virality estimate."
)


def _user_prompt(topic: str, n: int) -> str:
    return (
        f"Write {n} different YouTube Shorts scripts about: {topic}.\n"
        "Make each one a distinct angle or example. Aim for a 90+ virality score on every one."
    )


def _clean_tags(tags) -> List[str]:
    out: List[str] = []
    for t in tags or []:
        t = re.sub(r"[^0-9a-zA-Z]", "", str(t)).lower()
        if t and t not in out:
            out.append(t)
    return out[:6] or ["shorts", "finance"]


def _scripts_gemini(topic: str, n: int) -> List[Script]:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel

    class S(BaseModel):
        title: str
        hook: str
        script: str
        score: int
        hashtags: list[str]

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_user_prompt(topic, n),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=list[S],
        ),
    )
    return [Script(s.title.strip().strip('"'), s.hook.strip(), s.script.strip(),
                   max(0, min(100, s.score)), _clean_tags(s.hashtags))
            for s in (resp.parsed or [])]


def _scripts_claude(topic: str, n: int) -> List[Script]:
    import anthropic
    from pydantic import BaseModel

    class S(BaseModel):
        title: str
        hook: str
        script: str
        score: int
        hashtags: list[str]

    class Scripts(BaseModel):
        scripts: list[S]

    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=HIGHLIGHT_MODEL, max_tokens=16000, thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _user_prompt(topic, n)}],
        output_format=Scripts,
    )
    return [Script(s.title.strip().strip('"'), s.hook.strip(), s.script.strip(),
                   max(0, min(100, s.score)), _clean_tags(s.hashtags))
            for s in resp.parsed_output.scripts]


def generate_scripts(topic: str, n: int) -> List[Script]:
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return _scripts_gemini(topic, n)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _scripts_claude(topic, n)
    raise RuntimeError(
        "Generate mode needs an AI key to write scripts. Add a free GEMINI_API_KEY "
        "(aistudio.google.com/apikey) as a Codespaces secret, then restart."
    )


def _compose(work: Path, words, caption_style: str) -> Path:
    """Render audio.mp3 + captions over a solid 9:16 background."""
    duration = ffprobe_duration(work / "audio.mp3")
    (work / "captions.ass").write_text(
        build_ass(words, highlight=True, preset=caption_style), encoding="utf-8")
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={BACKGROUND}:s=1080x1920:r=30:d={duration:.2f}",
        "-i", "audio.mp3",
        "-vf", "subtitles=captions.ass",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
        "final.mp4",
    ], cwd=str(work))
    return work / "final.mp4"


def generate_clips(job, update: Callable[..., None]) -> None:
    """Generate narrated Shorts for a job and populate job.clips."""
    update(stage="script", progress=10, message="Writing scripts…")
    scripts = generate_scripts(job.topic or "finance concepts and recent events", job.num_clips)
    if not scripts:
        raise RuntimeError("No scripts were generated. Try a more specific topic.")

    clips: List[dict] = []
    total = len(scripts)
    for i, s in enumerate(scripts):
        update(stage="render", progress=20 + int(70 * i / max(1, total)),
               message=f"Voicing & rendering clip {i + 1} of {total}…")
        clip_id = f"{job.id}-{i + 1}"
        work = WORK_DIR / clip_id
        work.mkdir(parents=True, exist_ok=True)

        audio_bytes, words = synthesize(s.script)
        (work / "audio.mp3").write_bytes(audio_bytes)
        final = _compose(work, words, job.caption_style)

        out_name = f"{clip_id}.mp4"
        shutil.copy(final, OUTPUT_DIR / out_name)
        duration = ffprobe_duration(OUTPUT_DIR / out_name)
        shutil.rmtree(work, ignore_errors=True)

        clips.append({
            "id": clip_id, "index": i + 1, "title": s.title,
            "score": s.score, "reason": s.hook, "hashtags": s.hashtags,
            "start": 0.0, "end": round(duration, 2), "output": out_name,
        })
        update(clips=list(clips))

    update(stage="done", progress=100, message="Done!", clips=clips)
