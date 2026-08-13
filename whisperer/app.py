"""Application coordinator wiring hotkeys, recording, transcription, and output."""

from __future__ import annotations

import argparse
import sys
import threading
from dataclasses import replace

import numpy as np
from openai import OpenAI

from whisperer.audio_recorder import AudioRecorder
from whisperer.config import WhispererConfig, resolve_openai_api_key
from whisperer.hotkey_listener import HotkeyListener, parse_key
from whisperer.output_manager import OutputManager
from whisperer.transcription.base import Transcriber
from whisperer.translation_manager import TranslationManager

NEW_PARAGRAPH_COMMAND = "New paragraph."


class WhispererApp:
    """Coordinates the record → transcribe → (translate) → paste pipeline."""

    def __init__(
        self,
        config: WhispererConfig,
        recorder: AudioRecorder,
        transcriber: Transcriber,
        translator: TranslationManager | None,
        output: OutputManager,
    ) -> None:
        self._config = config
        self._recorder = recorder
        self._transcriber = transcriber
        self._translator = translator
        self._output = output
        self._translate_requested = False
        self._keep_next_on_clipboard = False

    def run(self) -> None:
        listener = HotkeyListener(
            record_key=parse_key(self._config.record_key),
            translate_key=parse_key(self._config.translate_key),
            on_record_start=self._start_recording,
            on_record_stop=self._stop_recording,
            on_translate_tap=self._request_translation,
        )
        print(
            f"Whisperer ready. Hold {self._config.record_key} to record; "
            f"tap {self._config.translate_key} while recording to translate. Ctrl+C quits."
        )
        listener.run()

    def _start_recording(self) -> None:
        self._translate_requested = False
        self._recorder.start()
        print("Recording started...")

    def _stop_recording(self) -> None:
        audio = self._recorder.stop()
        print("Recording stopped.")
        duration_seconds = len(audio) / self._config.sample_rate

        # A quick tap (too short to be speech) arms clipboard-keep for the next transcript.
        if duration_seconds < self._config.tap_duration_seconds:
            self._keep_next_on_clipboard = True
            print("Quick tap: the next transcript will stay on the clipboard.")
            return
        if duration_seconds < self._config.min_duration_seconds:
            print(f"Discarding recording shorter than {self._config.min_duration_seconds}s.")
            return

        translate = self._translate_requested
        keep_on_clipboard = self._keep_next_on_clipboard
        self._keep_next_on_clipboard = False
        worker = threading.Thread(
            target=self._process_recording,
            args=(audio, translate, keep_on_clipboard),
            daemon=True,
        )
        worker.start()

    def _request_translation(self) -> None:
        self._translate_requested = True
        print("Translation requested for this recording.")

    def _process_recording(
        self, audio: np.ndarray, translate: bool, keep_on_clipboard: bool
    ) -> None:
        try:
            print("Transcribing...")
            transcript = self._transcriber.transcribe(audio, self._config.sample_rate)
            transcript = transcript.replace(NEW_PARAGRAPH_COMMAND, "\n\n").strip()
            if translate:
                transcript = self._translate_or_warn(transcript)
            print(f"Transcript:\n{transcript}")
            if transcript:
                self._output.paste(transcript, keep_on_clipboard=keep_on_clipboard)
        except Exception as error:  # keep the hotkey loop alive whatever goes wrong
            print(f"Transcription failed: {error}")

    def _translate_or_warn(self, transcript: str) -> str:
        if self._translator is None:
            print("Translation unavailable: no OpenAI API key configured.")
            return transcript
        print("Translating...")
        return self._translator.translate(transcript)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="whisperer",
        description="Hold a key to dictate; the transcript is pasted at your cursor.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="transcribe locally with faster-whisper instead of the OpenAI API",
    )
    return parser.parse_args()


def _build_transcriber(config: WhispererConfig, client: OpenAI | None) -> Transcriber:
    if config.use_local_backend:
        from whisperer.transcription.local_transcriber import LocalWhisperTranscriber

        print(f"Loading local Whisper model '{config.local_model_size}'...")
        return LocalWhisperTranscriber(config.local_model_size, config.transcription_prompt)

    from whisperer.transcription.openai_transcriber import OpenAITranscriber

    if client is None:
        print(
            "No OpenAI API key found. Set the OPENAI_API_KEY environment variable "
            "or create openai_api_key.txt in the directory you run whisperer from."
        )
        sys.exit(1)
    return OpenAITranscriber(client, config.transcription_model, config.transcription_prompt)


def main() -> None:
    arguments = _parse_arguments()
    config = WhispererConfig.load()
    if arguments.local:
        config = replace(config, use_local_backend=True)

    api_key = resolve_openai_api_key()
    client = OpenAI(api_key=api_key) if api_key else None
    translator = (
        TranslationManager(client, config.translation_model, config.translation_system_prompt)
        if client
        else None
    )

    app = WhispererApp(
        config=config,
        recorder=AudioRecorder(config.sample_rate),
        transcriber=_build_transcriber(config, client),
        translator=translator,
        output=OutputManager(),
    )
    app.run()


if __name__ == "__main__":
    main()
