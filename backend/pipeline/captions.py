"""Turn timed words into a styled .ass subtitle file for burning into video.

The default style is the punchy, centred, big-bold caption look common to
Shorts/Reels/TikTok, with an optional karaoke-style highlight on the word
currently being spoken.
"""
from __future__ import annotations

from typing import List

from .transcribe import Word

# ASS colours are &HAABBGGRR (alpha, blue, green, red — note the byte order).
COLOR_WHITE = "&H00FFFFFF"
COLOR_HIGHLIGHT = "&H0000FFFF"  # bright yellow

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,96,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,4,2,90,90,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _fmt_ts(t: float) -> str:
    """Format seconds as ASS timestamp h:mm:ss.cc (centiseconds)."""
    if t < 0:
        t = 0.0
    hours = int(t // 3600)
    minutes = int((t % 3600) // 60)
    seconds = t % 60
    return f"{hours:d}:{minutes:02d}:{seconds:05.2f}"


def group_words(
    words: List[Word],
    max_words: int = 4,
    max_gap: float = 0.6,
    max_dur: float = 2.2,
) -> List[List[Word]]:
    """Group words into short on-screen chunks (a few words at a time)."""
    chunks: List[List[Word]] = []
    current: List[Word] = []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            dur = w["end"] - current[0]["start"]
            if len(current) >= max_words or gap > max_gap or dur > max_dur:
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


def _dialogue(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_fmt_ts(start)},{_fmt_ts(end)},Default,,0,0,0,,{text}"


def build_ass(words: List[Word], highlight: bool = True) -> str:
    """Build the full .ass document text from timed words."""
    lines = [_HEADER]
    for chunk in group_words(words):
        texts = [w["text"] for w in chunk]
        chunk_end = chunk[-1]["end"]

        if not highlight:
            lines.append(_dialogue(chunk[0]["start"], chunk_end, " ".join(texts)))
            continue

        # One dialogue event per word so the active word lights up in sequence.
        for i, word in enumerate(chunk):
            seg_start = word["start"]
            seg_end = chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk_end
            if seg_end <= seg_start:
                seg_end = seg_start + 0.05
            parts = []
            for j, t in enumerate(texts):
                if j == i:
                    parts.append(f"{{\\c{COLOR_HIGHLIGHT}}}{t}{{\\c{COLOR_WHITE}}}")
                else:
                    parts.append(t)
            lines.append(_dialogue(seg_start, seg_end, " ".join(parts)))

    return "\n".join(lines) + "\n"
