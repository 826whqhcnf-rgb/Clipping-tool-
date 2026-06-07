"""Pick the most clip-worthy moments from a transcript.

Primary path: ask Claude (via the Anthropic API) to read the timestamped
transcript and return the strongest self-contained moments, each with a title,
a virality score, and a one-line reason — the core of a Viblo-style auto-clipper.

Fallback (no ANTHROPIC_API_KEY): a speech-density heuristic so the tool still
produces clips offline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from ..config import GEMINI_MODEL, HIGHLIGHT_MODEL
from .transcribe import Word


@dataclass
class Highlight:
    start: float
    end: float
    title: str
    score: Optional[int]   # 0-100 virality estimate; None if unknown
    reason: str


def _timestamped_transcript(words: List[Word], group: int = 12) -> str:
    """Render words as timestamped lines: '[83.4] some words here'."""
    lines: List[str] = []
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        ts = chunk[0]["start"]
        text = " ".join(w["text"] for w in chunk)
        lines.append(f"[{ts:.1f}] {text}")
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You are an expert short-form video editor who finds viral moments in long "
    "videos for YouTube Shorts, TikTok, and Reels. You are given a timestamped "
    "transcript. Each line starts with the start time in seconds in square "
    "brackets. Identify the strongest standalone moments to clip.\n\n"
    "Rules for every clip you choose:\n"
    "- It must be self-contained: a complete thought, story, joke, insight, or hook.\n"
    "- Prefer moments with a strong hook in the first few seconds.\n"
    "- start and end are in seconds and must fall within the transcript's range.\n"
    "- Each clip must be between the given minimum and maximum length.\n"
    "- Clips must not overlap each other.\n"
    "- score is a 0-100 estimate of viral potential (higher = more shareable).\n"
    "- title is a punchy, scroll-stopping caption (<= 60 characters).\n"
    "- reason is one short sentence on why it will perform well.\n"
    "Return the clips ordered from highest to lowest score."
)


def _build_user_prompt(words: List[Word], num_clips: int, min_len: float, max_len: float) -> str:
    transcript = _timestamped_transcript(words)
    return (
        f"Pick the {num_clips} best clips from this transcript.\n"
        f"Each clip must be between {min_len:.0f} and {max_len:.0f} seconds long.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )


def _find_with_gemini(
    words: List[Word],
    num_clips: int,
    min_len: float,
    max_len: float,
) -> List[Highlight]:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel

    class ClipChoice(BaseModel):
        start: float
        end: float
        title: str
        score: int
        reason: str

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_build_user_prompt(words, num_clips, min_len, max_len),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=list[ClipChoice],
        ),
    )
    choices = response.parsed or []
    return [
        Highlight(
            start=c.start, end=c.end, title=c.title.strip(),
            score=max(0, min(100, c.score)), reason=c.reason.strip(),
        )
        for c in choices
    ]


def _find_with_claude(
    words: List[Word],
    num_clips: int,
    min_len: float,
    max_len: float,
) -> List[Highlight]:
    import anthropic
    from pydantic import BaseModel

    class ClipChoice(BaseModel):
        start: float
        end: float
        title: str
        score: int
        reason: str

    class ClipChoices(BaseModel):
        clips: list[ClipChoice]

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=HIGHLIGHT_MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(words, num_clips, min_len, max_len)}],
        output_format=ClipChoices,
    )
    choices = response.parsed_output.clips
    return [
        Highlight(
            start=c.start, end=c.end, title=c.title.strip(),
            score=max(0, min(100, c.score)), reason=c.reason.strip(),
        )
        for c in choices
    ]


def _find_by_density(
    words: List[Word],
    num_clips: int,
    min_len: float,
    max_len: float,
    duration: float,
) -> List[Highlight]:
    """Heuristic fallback: rank fixed windows by words-per-second."""
    target = (min_len + max_len) / 2

    # No speech detected — just split the timeline evenly.
    if not words:
        clips: List[Highlight] = []
        step = max(target, duration / max(1, num_clips))
        t = 0.0
        idx = 1
        while t < duration and len(clips) < num_clips:
            end = min(duration, t + min(max_len, step))
            clips.append(Highlight(t, end, f"Clip {idx}", None, "Even split (no speech detected)"))
            t = end
            idx += 1
        return clips

    # Build candidate windows starting at each word, ~target seconds long.
    candidates: List[Highlight] = []
    for i, w in enumerate(words):
        start = w["start"]
        end = start + target
        count = 0
        last = start
        for w2 in words[i:]:
            if w2["start"] > end:
                break
            count += 1
            last = w2["end"]
        length = min(max_len, max(min_len, last - start))
        density = count / max(1.0, length)
        score = int(min(100, density * 20))
        candidates.append(Highlight(start, start + length, "Highlight", score,
                                    "High speech density"))

    candidates.sort(key=lambda h: h.score or 0, reverse=True)

    # Greedily take the top non-overlapping windows.
    chosen: List[Highlight] = []
    for cand in candidates:
        if len(chosen) >= num_clips:
            break
        if all(cand.end <= c.start or cand.start >= c.end for c in chosen):
            chosen.append(cand)

    chosen.sort(key=lambda h: h.start)
    for idx, h in enumerate(chosen, 1):
        h.title = f"Highlight {idx}"
    return chosen


def find_highlights(
    words: List[Word],
    num_clips: int,
    min_len: float,
    max_len: float,
    duration: float,
) -> List[Highlight]:
    """Find up to `num_clips` highlights, clamped to the video's bounds.

    Provider preference: Gemini (free tier) -> Claude -> offline heuristic.
    """
    if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) and words:
        highlights = _find_with_gemini(words, num_clips, min_len, max_len)
    elif os.environ.get("ANTHROPIC_API_KEY") and words:
        highlights = _find_with_claude(words, num_clips, min_len, max_len)
    else:
        highlights = _find_by_density(words, num_clips, min_len, max_len, duration)

    # Clamp to valid ranges and drop anything degenerate.
    cleaned: List[Highlight] = []
    for h in highlights:
        start = max(0.0, min(h.start, duration))
        end = max(0.0, min(h.end, duration))
        if end - start < 1.0:
            continue
        h.start, h.end = start, end
        cleaned.append(h)
    return cleaned[:num_clips]
