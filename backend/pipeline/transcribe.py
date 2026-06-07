"""Speech-to-text with word-level timestamps via faster-whisper."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, TypedDict

from ..config import WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL


class Word(TypedDict):
    start: float
    end: float
    text: str


_model = None


def _get_model():
    """Lazily load the Whisper model once and reuse it across jobs."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE
        )
    return _model


def transcribe(audio_path: Path, language: Optional[str] = None) -> List[Word]:
    """Return a flat list of words with start/end times (seconds from clip start)."""
    model = _get_model()
    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )

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
    return words
