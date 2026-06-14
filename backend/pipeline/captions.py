"""Turn timed words into a styled .ass subtitle file for burning into video.

Produces the punchy, centred, big-bold caption look common to
Shorts/Reels/TikTok, with karaoke-style word highlighting and an optional
"pop" animation on the active word. Several visual presets are available.
"""
from __future__ import annotations

from typing import Dict, List

from .transcribe import Word

# ASS colours are &HAABBGGRR (alpha, blue, green, red — note the byte order).
# Lower alpha = more opaque (00 = solid, FF = transparent).

# Visual presets. Each defines the Default style and the active-word treatment.
PRESETS: Dict[str, dict] = {
    "karaoke": {  # white text, bright-yellow active word that pops — the classic
        "font": "Arial", "size": 92, "bold": -1,
        "primary": "&H00FFFFFF", "outline_col": "&H00000000", "back": "&H64000000",
        "border_style": 1, "outline": 6, "shadow": 3, "margin_v": 420,
        "highlight": "&H0000FFFF", "pop": True,
    },
    "clean": {  # plain white, no colour change — minimal and readable
        "font": "Arial", "size": 88, "bold": -1,
        "primary": "&H00FFFFFF", "outline_col": "&H00000000", "back": "&H64000000",
        "border_style": 1, "outline": 6, "shadow": 2, "margin_v": 420,
        "highlight": "&H00FFFFFF", "pop": False,
    },
    "boxed": {  # white text on a translucent black box, green active word
        "font": "Arial", "size": 82, "bold": -1,
        "primary": "&H00FFFFFF", "outline_col": "&H00000000", "back": "&HB0000000",
        "border_style": 3, "outline": 0, "shadow": 0, "margin_v": 440,
        "highlight": "&H0066FF66", "pop": True,
    },
}
DEFAULT_PRESET = "karaoke"


def _style_line(p: dict) -> str:
    """Build the V4+ Style line for a preset."""
    return (
        "Style: Default,{font},{size},{primary},&H000000FF,{outline_col},{back},"
        "{bold},0,0,0,100,100,0,0,{border_style},{outline},{shadow},2,90,90,{margin_v},1"
    ).format(**p)


def _header(p: dict) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"{_style_line(p)}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def _escape(text: str) -> str:
    """Make text safe to embed in an ASS dialogue line.

    `{` and `}` start/end override blocks and a backslash begins a tag, so a
    transcribed word containing any of them would corrupt the line — escape
    them. Also flatten any stray newlines.
    """
    return (
        text.replace("\\", "⧵")  # rare glyph stand-in; avoids tag parsing
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
        .strip()
    )


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
        if not w["text"].strip():
            continue
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


def build_ass(words: List[Word], highlight: bool = True, preset: str = DEFAULT_PRESET,
              header: str = "", header_end: float = 0.0) -> str:
    """Build the full .ass document text from timed words.

    If `header` is given, a persistent title banner is pinned near the top for
    the first `header_end` seconds (used by generated Shorts).
    """
    p = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
    lines = [_header(p)]

    if header and header_end > 0:
        banner = _escape(header)
        lines.append(
            f"Dialogue: 0,{_fmt_ts(0)},{_fmt_ts(header_end)},Default,,0,0,0,,"
            f"{{\\an8\\pos(540,170)\\fs58\\b1\\bord5\\shad0}}{banner}"
        )

    # Tag applied to the active word: switch colour, and optionally animate a pop.
    pop = "\\t(0,150,\\fscx116\\fscy116)" if p["pop"] else ""
    active_open = f"{{\\c{p['highlight']}{pop}}}"
    reset = "{\\r}"

    for chunk in group_words(words):
        texts = [_escape(w["text"]) for w in chunk]
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
            parts = [
                f"{active_open}{t}{reset}" if j == i else t
                for j, t in enumerate(texts)
            ]
            lines.append(_dialogue(seg_start, seg_end, " ".join(parts)))

    return "\n".join(lines) + "\n"
