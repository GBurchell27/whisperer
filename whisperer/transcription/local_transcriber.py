"""Offline transcription via faster-whisper (CTranslate2)."""

from __future__ import annotations

import numpy as np


class LocalWhisperTranscriber:
    """Runs a Whisper model locally. Expects 16 kHz mono float32 audio."""

    def __init__(self, model_size: str, prompt: str) -> None:
        # Deferred import: heavy, and only installed with the [local] extra.
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_size, compute_type="int8")
        self._prompt = prompt

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if sample_rate != 16000:
            raise ValueError("faster-whisper requires 16 kHz audio; set sample_rate = 16000.")
        segments, _ = self._model.transcribe(audio, initial_prompt=self._prompt)
        return "".join(segment.text for segment in segments)
