"""Speech-to-text with word-level timestamps via faster-whisper."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional, TypedDict

from ..config import WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL


class Word(TypedDict):
    start: float
    end: float
    text: str


_model = None


def _get_model():
    """Lazily load the Whisper model once and reuse it across jobs.

    The first call downloads the model (~75 MB for `tiny`); a blocked network
    surfaces as a clear, actionable error rather than a raw Hub traceback.
    """
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        try:
            _model = WhisperModel(
                WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if any(k in msg for k in ("huggingface", "Hub", "Forbidden", "snapshot", "connection")):
                raise RuntimeError(
                    f"Couldn't download the speech model '{WHISPER_MODEL}'. The "
                    "server needs internet access to fetch it the first time. "
                    "Try CLIP_WHISPER_MODEL=tiny, or turn off Auto captions."
                ) from exc
            raise
    return _model


def transcribe(
    audio_path: Path,
    language: Optional[str] = None,
    progress: Optional[Callable[[float], None]] = None,
) -> List[Word]:
    """Return a flat list of words with start/end times (seconds from clip start).

    `progress(fraction)` (0..1) is called as transcription advances, so long
    videos can show a moving progress bar instead of an opaque pause.
    """
    model = _get_model()
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )
    total = getattr(info, "duration", 0.0) or 0.0

    words: List[Word] = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                text = w.word.strip()
                if text:
                    words.append({"start": w.start, "end": w.end, "text": text})
        else:
            text = seg.text.strip()
            if text:
                words.append({"start": seg.start, "end": seg.end, "text": text})
        if progress and total:
            progress(min(1.0, seg.end / total))
    return words
