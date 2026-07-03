"""Unit tests for the pure-logic parts of the pipeline (no ffmpeg/network)."""
import os

import pytest

# Ensure the heuristic path is used regardless of the runner's environment.
for _k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)

from backend.utils import parse_timestamp
from backend.pipeline.captions import build_ass, group_words, _escape
from backend.pipeline.reframe import reframe_filter, smart_crop_vf
from backend.pipeline.render import slice_words
from backend.pipeline.highlights import (
    find_highlights, _dedupe, _title_from_transcript, _keyword_hashtags,
    snap_to_speech, Highlight,
)


def _words(n, step=0.4, dur=0.35):
    return [{"start": i * step, "end": i * step + dur, "text": f"w{i}"} for i in range(n)]


# --- utils -------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("83", 83.0), ("1:23", 83.0), ("1:02:03", 3723.0), ("45.5", 45.5), ("", None), (None, None),
])
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected


def test_parse_timestamp_invalid():
    with pytest.raises(ValueError):
        parse_timestamp("12:ab")


# --- reframe -----------------------------------------------------------------
def test_reframe_kinds():
    assert reframe_filter("crop")[0] == "vf"
    assert reframe_filter("pad")[0] == "vf"
    kind, f = reframe_filter("blur")
    assert kind == "complex" and f.endswith("[v]")


def test_reframe_invalid():
    with pytest.raises(ValueError):
        reframe_filter("nope")


def test_smart_crop_centers_and_clamps():
    # 1280x720 -> 1080x1920: scaled width ~3414, crop window 1080 wide.
    left = smart_crop_vf(0.0, 1280, 720)
    center = smart_crop_vf(0.5, 1280, 720)
    right = smart_crop_vf(1.0, 1280, 720)
    assert left.endswith(":0:0")               # clamped to the left edge
    assert "crop=1080:1920:" in center         # correct output size
    # right edge clamp: x == scaled_w - 1080, never beyond
    x_right = int(right.split(":")[-2])
    scaled_w = int(center.split("scale=")[1].split(":")[0])
    assert x_right == scaled_w - 1080
    # off-centre subject shifts the crop right of centre
    x_center = int(center.split(":")[-2])
    assert int(smart_crop_vf(0.8, 1280, 720).split(":")[-2]) > x_center


# --- captions ----------------------------------------------------------------
def test_escape_neutralizes_ass_tags():
    # Braces and backslashes would otherwise corrupt the dialogue line.
    out = _escape("a{b}c\\d")
    assert "{" not in out and "}" not in out and "\\" not in out


def test_build_ass_structure_and_presets():
    words = _words(10)
    ass = build_ass(words, highlight=True, preset="karaoke")
    assert "PlayResY: 1920" in ass
    assert ass.count("Dialogue:") == 10  # one event per word when highlighting
    assert "\\t(0,150" in ass            # karaoke pop animation present

    plain = build_ass(words, highlight=False)
    assert 0 < plain.count("Dialogue:") < 10  # grouped into chunks

    clean = build_ass(words, highlight=True, preset="clean")
    assert "\\t(0,150" not in clean       # clean preset has no pop


def test_build_ass_unknown_preset_falls_back():
    assert "Dialogue:" in build_ass(_words(4), preset="does-not-exist")


def test_build_ass_title_banner_is_added_and_escaped():
    ass = build_ass(_words(4), header="Why {x} matters\\now", header_end=5.0)
    assert "Style: Title," in ass and ",Title,," in ass  # top banner uses Title style
    assert "{x}" not in ass and "\\now" not in ass        # special chars escaped
    # no banner dialogue when header_end is 0
    assert ",Title,," not in build_ass(_words(4), header="ignored", header_end=0)


def test_group_words_respects_limits():
    chunks = group_words(_words(12), max_words=4)
    assert all(len(c) <= 4 for c in chunks)
    assert sum(len(c) for c in chunks) == 12


# --- render ------------------------------------------------------------------
def test_slice_words_offsets_to_clip_relative():
    sl = slice_words(_words(10), 1.0, 2.0)
    assert sl and all(w["start"] >= 0 for w in sl)
    assert min(w["start"] for w in sl) < 1.0  # re-based to start of clip


# --- highlights --------------------------------------------------------------
def test_dedupe_removes_overlaps_keeps_best():
    hs = [
        Highlight(0, 10, "a", 90, ""),
        Highlight(5, 15, "b", 50, ""),   # overlaps a -> dropped
        Highlight(20, 30, "c", 70, ""),
    ]
    chosen = _dedupe(hs, num_clips=5)
    assert [h.title for h in chosen] == ["a", "c"]  # sorted by start, no overlap


def test_find_highlights_clamps_and_bounds():
    words = _words(120, step=0.5)  # ~60s of speech
    hl = find_highlights(words, num_clips=3, min_len=5, max_len=15, duration=60.0)
    assert 1 <= len(hl) <= 3
    for h in hl:
        assert 0 <= h.start < h.end <= 60.0
        assert (h.end - h.start) <= 15.0 + 0.01
        assert h.hashtags  # heuristic backfills hashtags


def test_title_from_transcript_strips_filler_and_caps():
    ws = [{"start": i, "end": i + 0.5, "text": w}
          for i, w in enumerate("so this is the wildest story you will ever hear today".split())]
    title = _title_from_transcript(ws, 0, 100)
    assert title and title[0].isupper()
    assert not title.lower().startswith("so ")          # filler removed
    assert not title.endswith(".")


def test_heuristic_titles_are_not_generic():
    ws = [{"start": i * 0.4, "end": i * 0.4 + 0.35, "text": w}
          for i, w in enumerate(("people always ask how to grow fast the answer is consistency " * 12).split())]
    hl = find_highlights(ws, num_clips=2, min_len=8, max_len=20, duration=40.0)
    for h in hl:
        assert not h.title.lower().startswith(("highlight", "clip"))
        assert "shorts" in h.hashtags


def test_keyword_hashtags_dedupe_and_cap():
    ws = [{"start": i, "end": i + 0.5, "text": w}
          for i, w in enumerate("growth growth mindset mindset success success habits".split())]
    tags = _keyword_hashtags(ws, 0, 100)
    assert tags[0] == "shorts" and len(tags) == len(set(tags)) and len(tags) <= 6


def test_build_ass_watermark():
    ass = build_ass(_words(4), watermark="@creator", duration=10.0)
    assert "Style: WM," in ass and ",WM,," in ass and "@creator" in ass
    # watermark-only (captions off) still yields a valid event
    only = build_ass([], watermark="@creator", duration=10.0)
    assert ",WM,," in only
    # no watermark event without a duration
    assert ",WM,," not in build_ass(_words(4), watermark="@creator", duration=0)


def test_snap_to_speech():
    words = [{"start": 5.0, "end": 5.4, "text": "a"}, {"start": 5.5, "end": 5.9, "text": "b"},
             {"start": 9.0, "end": 9.5, "text": "c"}]
    # window cutting into the first word snaps back to its start (minus pad)
    s, e = snap_to_speech(words, 5.2, 9.2, duration=60.0)
    assert s == pytest.approx(4.8) and e == pytest.approx(9.7)
    # no words in window -> unchanged
    assert snap_to_speech(words, 20.0, 30.0, duration=60.0) == (20.0, 30.0)
    # never exceeds video bounds
    s2, e2 = snap_to_speech(words, 0.0, 100.0, duration=9.6)
    assert s2 >= 0.0 and e2 <= 9.6


def test_broll_query_and_no_key_fallback():
    import os
    from pathlib import Path
    from backend.pipeline.broll import fetch_broll
    from backend.pipeline.generate import _broll_query, Script
    os.environ.pop("PEXELS_API_KEY", None)
    assert fetch_broll("stock market", Path("/tmp/none.mp4")) is None  # no key -> None
    q = _broll_query(Script("Why the stock market crashes", "", "", 90, ["stocks"]))
    assert q.endswith("finance") and "market" in q


def test_find_highlights_no_speech_even_split():
    hl = find_highlights([], num_clips=3, min_len=15, max_len=60, duration=120.0)
    assert len(hl) == 3
    assert all(h.score is None for h in hl)
