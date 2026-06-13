"""Pick the most clip-worthy moments from a transcript.

Primary path: ask an LLM (Gemini's free tier, or Claude) to read the
timestamped transcript and return the strongest self-contained moments, each
with a title, a virality score, a reason, and ready-to-post hashtags — the core
of a Viblo-style auto-clipper.

Fallback (no API key): a speech-density heuristic so the tool still produces
clips offline.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from ..config import GEMINI_MODEL, HIGHLIGHT_MODEL
from .transcribe import Word

_STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "your", "with", "that",
    "this", "have", "from", "they", "what", "when", "will", "would", "there",
    "their", "about", "just", "like", "really", "gonna", "yeah", "okay",
}


@dataclass
class Highlight:
    start: float
    end: float
    title: str
    score: Optional[int]   # 0-100 virality estimate; None if unknown
    reason: str
    hashtags: List[str] = field(default_factory=list)


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
    "transcript where each line starts with its start time in seconds in square "
    "brackets. Identify the strongest standalone moments to clip.\n\n"
    "Rules for every clip you choose:\n"
    "- It must be self-contained: a complete thought, story, joke, insight, or "
    "surprising claim that makes sense on its own.\n"
    "- The first 1-2 seconds must be a strong hook that stops the scroll.\n"
    "- start and end are in seconds and must fall within the transcript's range.\n"
    "- Each clip must be between the given minimum and maximum length.\n"
    "- Clips must NOT overlap, and should be spread across different parts of "
    "the video rather than clustered together.\n"
    "- score is a 0-100 estimate of viral potential (higher = more shareable).\n"
    "- title is a punchy, curiosity-driving caption (<= 60 characters), no "
    "surrounding quotes.\n"
    "- reason is one short sentence on why it will perform well.\n"
    "- hashtags is 3-6 relevant lowercase tags WITHOUT the # symbol.\n"
    "Return the clips ordered from highest to lowest score."
)


def _build_user_prompt(words: List[Word], num_clips: int, min_len: float, max_len: float) -> str:
    transcript = _timestamped_transcript(words)
    return (
        f"Pick the {num_clips} best clips from this transcript.\n"
        f"Each clip must be between {min_len:.0f} and {max_len:.0f} seconds long.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )


def _clean_hashtags(tags) -> List[str]:
    out: List[str] = []
    for t in tags or []:
        t = re.sub(r"[^0-9a-zA-Z]", "", str(t)).lower()
        if t and t not in out:
            out.append(t)
    return out[:6]


def _find_with_gemini(words, num_clips, min_len, max_len) -> List[Highlight]:
    from google import genai
    from google.genai import types
    from pydantic import BaseModel

    class ClipChoice(BaseModel):
        start: float
        end: float
        title: str
        score: int
        reason: str
        hashtags: list[str]

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
            start=c.start, end=c.end, title=c.title.strip().strip('"'),
            score=max(0, min(100, c.score)), reason=c.reason.strip(),
            hashtags=_clean_hashtags(c.hashtags),
        )
        for c in choices
    ]


def _find_with_claude(words, num_clips, min_len, max_len) -> List[Highlight]:
    import anthropic
    from pydantic import BaseModel

    class ClipChoice(BaseModel):
        start: float
        end: float
        title: str
        score: int
        reason: str
        hashtags: list[str]

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
    return [
        Highlight(
            start=c.start, end=c.end, title=c.title.strip().strip('"'),
            score=max(0, min(100, c.score)), reason=c.reason.strip(),
            hashtags=_clean_hashtags(c.hashtags),
        )
        for c in response.parsed_output.clips
    ]


def _clip_text(words: List[Word], start: float, end: float) -> str:
    return " ".join(w["text"] for w in words if start <= w["start"] < end).strip()


def _title_from_transcript(words: List[Word], start: float, end: float, max_len: int = 70) -> Optional[str]:
    """Craft a readable title from what's actually said in the clip."""
    text = _clip_text(words, start, end)
    if not text:
        return None
    # Drop leading filler so the title opens on something meaningful.
    text = re.sub(r"^(so|and|but|um|uh|well|like|okay|right|you know|i mean)[,\s]+",
                  "", text, flags=re.I).strip()
    if not text:
        return None
    # Prefer ending at the first sentence break; otherwise trim on a word boundary.
    m = re.search(r"[.!?]", text)
    if m and m.start() + 1 <= max_len:
        text = text[: m.start()]
    elif len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].rstrip(",;:-")
        text += "…"
    text = text.strip().strip('"').rstrip(".")
    return text[:1].upper() + text[1:] if text else None


def _keyword_hashtags(words: List[Word], start: float, end: float) -> List[str]:
    """Hashtags for the heuristic path: '#shorts' plus the clip's key content words."""
    freq: dict[str, int] = {}
    for w in words:
        if w["start"] < start or w["end"] > end:
            continue
        tok = re.sub(r"[^a-zA-Z]", "", w["text"]).lower()
        if len(tok) >= 4 and tok not in _STOPWORDS:
            freq[tok] = freq.get(tok, 0) + 1
    top = sorted(freq, key=lambda k: freq[k], reverse=True)[:4]
    tags = ["shorts", *top, "viral"]
    # De-dupe while preserving order, cap at 6.
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:6]


def _find_by_density(words, num_clips, min_len, max_len, duration) -> List[Highlight]:
    """Heuristic fallback: rank fixed windows by words-per-second."""
    target = (min_len + max_len) / 2

    if not words:  # no speech — split the timeline evenly
        clips: List[Highlight] = []
        step = max(target, duration / max(1, num_clips))
        t, idx = 0.0, 1
        while t < duration and len(clips) < num_clips:
            end = min(duration, t + min(max_len, step))
            clips.append(Highlight(t, end, f"Clip {idx}", None,
                                   "Even split (no speech detected)", ["shorts"]))
            t, idx = end, idx + 1
        return clips

    candidates: List[Highlight] = []
    for i, w in enumerate(words):
        start = w["start"]
        end = start + target
        count, last = 0, start
        for w2 in words[i:]:
            if w2["start"] > end:
                break
            count += 1
            last = w2["end"]
        length = min(max_len, max(min_len, last - start))
        density = count / max(1.0, length)
        candidates.append(Highlight(start, start + length, "Highlight",
                                    int(min(100, density * 20)), "High speech density"))

    candidates.sort(key=lambda h: h.score or 0, reverse=True)
    return candidates  # overlap removal + numbering happens in find_highlights


def _dedupe(highlights: List[Highlight], num_clips: int) -> List[Highlight]:
    """Greedily keep the highest-scoring non-overlapping clips."""
    chosen: List[Highlight] = []
    for cand in sorted(highlights, key=lambda h: h.score or 0, reverse=True):
        if len(chosen) >= num_clips:
            break
        if all(cand.end <= c.start or cand.start >= c.end for c in chosen):
            chosen.append(cand)
    chosen.sort(key=lambda h: h.start)
    return chosen


def find_highlights(
    words: List[Word],
    num_clips: int,
    min_len: float,
    max_len: float,
    duration: float,
) -> List[Highlight]:
    """Find up to `num_clips` highlights, clamped, length-bounded, non-overlapping.

    Provider preference: Gemini (free tier) -> Claude -> offline heuristic.
    """
    if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) and words:
        highlights = _find_with_gemini(words, num_clips, min_len, max_len)
    elif os.environ.get("ANTHROPIC_API_KEY") and words:
        highlights = _find_with_claude(words, num_clips, min_len, max_len)
    else:
        highlights = _find_by_density(words, num_clips, min_len, max_len, duration)

    # Clamp to bounds and enforce length limits; drop anything degenerate.
    cleaned: List[Highlight] = []
    for h in highlights:
        h.start = max(0.0, min(h.start, duration))
        h.end = min(duration, h.end)
        if h.end - h.start > max_len:           # too long: cap from the start
            h.end = h.start + max_len
        if h.end - h.start < min(min_len, 2.0):  # too short to be useful
            continue
        cleaned.append(h)

    chosen = _dedupe(cleaned, num_clips)

    # Backfill hashtags / titles for the heuristic path.
    for idx, h in enumerate(chosen, 1):
        if not h.hashtags:
            h.hashtags = _keyword_hashtags(words, h.start, h.end)
        # Replace generic placeholder titles with a real spoken-phrase title.
        if h.title.strip().lower().startswith(("highlight", "clip")) or not h.title.strip():
            h.title = _title_from_transcript(words, h.start, h.end) or f"Highlight {idx}"
    return chosen
