"""Microphone capture that accumulates audio while a recording is active."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import sounddevice as sd


class AudioRecorder:
    """Records mono audio from the default input device between start() and stop()."""

    def __init__(
        self,
        sample_rate: int,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._on_status = on_status
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        self._chunks = []
        self._stream = sd.InputStream(
            callback=self._on_audio_chunk, channels=1, samplerate=self._sample_rate
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop capturing and return the recording as a mono float32 array."""
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks, axis=0).flatten().astype(np.float32)

    def _on_audio_chunk(self, indata: np.ndarray, frames, time, status) -> None:
        if status and self._on_status:
            self._on_status(str(status))
        if indata.shape[1] == 1:
            self._chunks.append(indata.copy())
