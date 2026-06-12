"""Find where the speaker is, so a 9:16 crop can keep them in frame.

Uses OpenCV's bundled Haar cascade (no model download — works offline). If
OpenCV isn't installed or no face is found, callers fall back to a center crop.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional


def detect_face_center(source: Path, start: float, end: float, samples: int = 12) -> Optional[float]:
    """Return the speaker's horizontal centre as a 0..1 fraction of width.

    Samples a handful of frames across [start, end], detects the largest face
    in each, and returns the median centre. None if OpenCV is unavailable or no
    face is found in any sample.
    """
    try:
        import cv2  # type: ignore
    except Exception:
        return None

    cascade_file = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    cascade = cv2.CascadeClassifier(cascade_file)
    if cascade.empty():
        return None

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return None

    try:
        centers = []
        span = max(0.1, end - start)
        for i in range(samples):
            t = start + span * (i + 0.5) / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5,
                minSize=(max(20, w // 20), max(20, h // 20)),
            )
            if len(faces) == 0:
                continue
            fx, _fy, fw, _fh = max(faces, key=lambda r: r[2] * r[3])
            centers.append((fx + fw / 2.0) / w)
        return statistics.median(centers) if centers else None
    finally:
        cap.release()
