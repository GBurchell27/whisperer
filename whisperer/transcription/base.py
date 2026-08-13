"""Shared interface for transcription backends."""

from __future__ import annotations

from typing import Protocol

import numpy as np


class Transcriber(Protocol):
    """Turns recorded audio into text."""

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...
