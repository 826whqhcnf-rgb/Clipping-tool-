"""Build ffmpeg filters that turn landscape video into vertical 9:16.

Three modes:
  - "blur": video fit to width over a zoomed, blurred copy of itself (the most
            popular Shorts look — nothing gets cropped away).
  - "crop": scale to fill height, then center-crop the sides off.
  - "pad" : letterbox the whole frame onto a black background.
"""
from __future__ import annotations

from typing import Tuple

from ..config import OUTPUT_H, OUTPUT_W


def reframe_filter(mode: str, w: int = OUTPUT_W, h: int = OUTPUT_H) -> Tuple[str, str]:
    """Return (kind, filter_string).

    `kind` is "vf" for a simple -vf filter or "complex" for a -filter_complex
    graph whose final video pad is labelled [v].
    """
    if mode == "crop":
        # Scale up to cover the height, then crop the overflowing width.
        return "vf", (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}"
        )

    if mode == "pad":
        return "vf", (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )

    if mode == "blur":
        return "complex", (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},gblur=sigma=24[bgblur];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgscaled];"
            f"[bgblur][fgscaled]overlay=(W-w)/2:(H-h)/2[v]"
        )

    raise ValueError(f"Unknown reframe mode: {mode!r}")
