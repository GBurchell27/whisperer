"""Transcription via OpenAI's hosted audio API."""

from __future__ import annotations

import io

import numpy as np
import soundfile
from openai import OpenAI


class OpenAITranscriber:
    """Sends recorded audio to an OpenAI transcription model as an in-memory FLAC."""

    def __init__(self, client: OpenAI, model: str, prompt: str) -> None:
        self._client = client
        self._model = model
        self._prompt = prompt

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        flac_buffer = io.BytesIO()
        soundfile.write(flac_buffer, audio, sample_rate, format="FLAC")
        flac_buffer.seek(0)
        response = self._client.audio.transcriptions.create(
            model=self._model,
            file=("recording.flac", flac_buffer),
            prompt=self._prompt,
        )
        return response.text
