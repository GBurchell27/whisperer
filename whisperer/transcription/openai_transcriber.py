"""Transcription via OpenAI's hosted audio API."""

from __future__ import annotations

import io
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile
from openai import OpenAI


class OpenAITranscriber:
    """Sends recorded audio to an OpenAI transcription model as an in-memory FLAC."""

    def __init__(
        self, client: OpenAI, model: str, prompt: str,
        *, chunk_seconds: float = 30.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._prompt = prompt
        if chunk_seconds < 0:
            raise ValueError("transcription_chunk_seconds must be zero or positive")
        self._chunk_seconds = chunk_seconds
        self._on_progress = on_progress

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if not len(audio) or np.max(np.abs(audio)) < 0.0001:
            return ""  # Don't send digital silence to a model that may invent speech.
        chunks = self._split_at_pauses(audio, sample_rate)
        count = len(chunks)
        if self._on_progress and count > 1:
            self._on_progress(f"{count} chunks, up to 2 requests in parallel")

        def transcribe_chunk(index: int) -> str:
            chunk = chunks[index]
            started = time.perf_counter()
            text = self._transcribe_chunk(chunk, sample_rate)
            if self._on_progress:
                self._on_progress(
                    f"chunk {index + 1}/{count}  {len(chunk) / sample_rate:.1f}s audio"
                    f"  request={time.perf_counter() - started:.1f}s"
                )
            return text.strip()

        if count == 1:
            return transcribe_chunk(0)
        # executor.map preserves speech order even when later requests finish first.
        # If any chunk fails, propagate the error rather than paste a partial note.
        with ThreadPoolExecutor(max_workers=2) as pool:
            return " ".join(part for part in pool.map(transcribe_chunk, range(count)) if part)

    def _split_at_pauses(self, audio: np.ndarray, sample_rate: int) -> list[np.ndarray]:
        if not self._chunk_seconds:
            return [audio]
        target = max(1, int(self._chunk_seconds * sample_rate))
        window = max(1, int(0.08 * sample_rate))
        chunks = []
        start = 0
        while len(audio) - start > target * 1.5:
            low = start + int(target * 2 / 3)
            high = min(start + int(target * 4 / 3), len(audio))
            count = (high - low) // window
            if not count:
                break
            frames = audio[low:low + count * window].reshape(count, window)
            rms = np.sqrt(np.mean(frames * frames, axis=1))
            # Split only at a quiet pause, not in the middle of a spoken word.
            threshold = min(0.005, float(np.percentile(rms, 75)) * 0.2)
            quiet = np.flatnonzero(rms <= max(0.0001, threshold))
            if not len(quiet):
                break  # Keep continuous speech intact if no safe boundary exists.
            positions = low + quiet * window + window // 2
            cut = int(positions[np.argmin(np.abs(positions - (start + target)))])
            chunks.append(audio[start:cut])
            start = cut
        chunks.append(audio[start:])
        return chunks

    def _transcribe_chunk(self, audio: np.ndarray, sample_rate: int) -> str:
        if not len(audio) or np.max(np.abs(audio)) < 0.0001:
            return ""
        flac_buffer = io.BytesIO()
        soundfile.write(flac_buffer, audio, sample_rate, format="FLAC")
        flac_buffer.seek(0)
        request = {"model": self._model, "file": ("recording.flac", flac_buffer)}
        if self._prompt:
            request["prompt"] = self._prompt
        response = self._client.audio.transcriptions.create(**request)
        return response.text
