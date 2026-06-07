"""Small shared helpers: binary detection, timestamp parsing, subprocess runner."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence

_NUMBER = re.compile(r"\d+(\.\d+)?$")


def have_binary(name: str) -> bool:
    """Return True if an executable is on PATH (e.g. ffmpeg/ffprobe)."""
    return shutil.which(name) is not None


def parse_timestamp(value: Optional[str]) -> Optional[float]:
    """Parse a human timestamp into seconds.

    Accepts plain seconds ("83", "83.5"), "mm:ss" ("1:23") or "hh:mm:ss"
    ("1:02:03"), each with an optional fractional part. Empty/None -> None.
    """
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    parts = value.split(":")
    if not all(_NUMBER.match(p) for p in parts):
        raise ValueError(f"Invalid timestamp: {value!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def run(cmd: Sequence[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a command, raising RuntimeError with trimmed stderr on failure."""
    proc = subprocess.run(
        list(cmd), cwd=cwd, capture_output=True, text=True
    )
    if proc.returncode != 0:
        head = " ".join(str(c) for c in list(cmd)[:3])
        raise RuntimeError(f"Command failed ({head} …):\n{proc.stderr[-4000:]}")
    return proc


def ffprobe_duration(path: Path) -> float:
    """Return the duration of a media file in seconds."""
    proc = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    return float(json.loads(proc.stdout)["format"]["duration"])
