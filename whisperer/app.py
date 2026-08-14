"""Application coordinator wiring hotkeys, recording, transcription, and output."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import replace

import numpy as np
from openai import OpenAI

from whisperer.audio_recorder import AudioRecorder
from whisperer.config import CONFIG_FILE, WhispererConfig, resolve_openai_api_key
from whisperer.console_logger import ConsoleLogger
from whisperer.hotkey_listener import HotkeyListener, parse_key
from whisperer.output_manager import OutputManager
from whisperer.transcription.base import Transcriber
from whisperer.translation_manager import TranslationManager

NEW_PARAGRAPH_COMMAND = "New paragraph."


class WhispererApp:
    """Coordinates the record → transcribe → (rewrite) → paste pipeline."""

    def __init__(
        self,
        config: WhispererConfig,
        recorder: AudioRecorder,
        transcriber: Transcriber,
        translator: TranslationManager | None,
        output: OutputManager,
        logger: ConsoleLogger,
    ) -> None:
        self._config = config
        self._recorder = recorder
        self._transcriber = transcriber
        self._translator = translator
        self._output = output
        self._log = logger
        self._rewrite_mode: str | None = None
        self._keep_next_on_clipboard = False

    def run(self) -> None:
        listener = HotkeyListener(
            record_key=parse_key(self._config.record_key),
            on_record_start=self._start_recording,
            on_record_stop=self._stop_recording,
            recording_taps={
                parse_key(self._config.translate_key): self._request_translation,
                parse_key(self._config.email_key): self._request_email,
            },
        )
        self._log_startup()
        listener.run()

    def _log_startup(self) -> None:
        backend = "local" if self._config.use_local_backend else "openai"
        transcribe_model = (
            self._config.local_model_size
            if self._config.use_local_backend
            else self._config.transcription_model
        )
        self._log.info(
            "ready",
            f"hold {self._config.record_key} | "
            f"tap {self._config.translate_key} to translate | "
            f"tap {self._config.email_key} for email | Ctrl+C quits",
        )
        self._log.info("backend", f"{backend}  transcribe={transcribe_model}")
        if self._translator is None:
            self._log.warn("rewrite", "unavailable (no OpenAI API key)")
        else:
            effort = self._config.rewrite_reasoning_effort or "model default"
            self._log.info(
                "rewrite",
                f"model={self._config.translation_model}  effort={effort}",
            )

    def _start_recording(self) -> None:
        self._rewrite_mode = None
        self._log.blank()
        self._recorder.start()
        self._log.info("record", "started")

    def _stop_recording(self) -> None:
        audio = self._recorder.stop()
        duration_seconds = len(audio) / self._config.sample_rate
        if duration_seconds < self._config.tap_duration_seconds:
            self._keep_next_on_clipboard = True
            self._log.info(
                "record",
                f"stopped  {duration_seconds:.1f}s  quick tap -> keep next on clipboard",
            )
            return
        if duration_seconds < self._config.min_duration_seconds:
            self._log.warn(
                "discard",
                f"{duration_seconds:.1f}s < {self._config.min_duration_seconds:.1f}s minimum",
            )
            return
        self._queue_processing(audio, duration_seconds)

    def _queue_processing(self, audio: np.ndarray, duration_seconds: float) -> None:
        rewrite_mode = self._rewrite_mode
        keep_on_clipboard = self._keep_next_on_clipboard
        self._keep_next_on_clipboard = False
        mode = rewrite_mode or "verbatim"
        self._log.info("record", f"stopped  {duration_seconds:.1f}s  -> {mode}")
        worker = threading.Thread(
            target=self._process_recording,
            args=(audio, rewrite_mode, keep_on_clipboard),
            daemon=True,
        )
        worker.start()

    def _request_translation(self) -> None:
        self._rewrite_mode = "translate"
        self._log.info("mode", "translate")

    def _request_email(self) -> None:
        self._rewrite_mode = "email"
        self._log.info("mode", "email")

    def _process_recording(
        self, audio: np.ndarray, rewrite_mode: str | None, keep_on_clipboard: bool
    ) -> None:
        try:
            transcript = self._transcribe(audio)
            if rewrite_mode:
                transcript = self._rewrite_or_warn(transcript, rewrite_mode)
            self._paste_or_skip(transcript, keep_on_clipboard)
        except Exception as error:  # keep the hotkey loop alive whatever goes wrong
            self._log.error("failed", f"{type(error).__name__}: {error}")

    def _transcribe(self, audio: np.ndarray) -> str:
        duration_seconds = len(audio) / self._config.sample_rate
        self._log.info("transcribe", f"sending {duration_seconds:.1f}s of audio")
        started = time.perf_counter()
        transcript = self._transcriber.transcribe(audio, self._config.sample_rate)
        transcript = transcript.replace(NEW_PARAGRAPH_COMMAND, "\n\n").strip()
        elapsed = time.perf_counter() - started
        self._log.info("transcript", f"{elapsed:.1f}s  {len(transcript)} chars")
        self._log.text(transcript)
        return transcript

    def _rewrite_or_warn(self, transcript: str, mode: str) -> str:
        if self._translator is None:
            self._log.warn("rewrite", f"{mode} skipped (no OpenAI API key)")
            return transcript
        prompt = self._prompt_for_mode(mode)
        self._log.info("rewrite", f"{mode}  sending to {self._config.translation_model}")
        started = time.perf_counter()
        rewritten = self._translator.rewrite(transcript, prompt)
        elapsed = time.perf_counter() - started
        self._log.info("rewritten", f"{mode}  {elapsed:.1f}s  {len(rewritten)} chars")
        self._log.text(rewritten)
        return rewritten

    def _prompt_for_mode(self, mode: str) -> str:
        if mode == "email":
            return self._config.email_system_prompt
        return self._config.translation_system_prompt

    def _paste_or_skip(self, transcript: str, keep_on_clipboard: bool) -> None:
        if not transcript:
            self._log.warn("paste", "skipped (empty transcript)")
            return
        self._output.paste(transcript, keep_on_clipboard=keep_on_clipboard)
        clipboard = "kept on clipboard" if keep_on_clipboard else "clipboard restored"
        self._log.info("paste", f"done  {clipboard}")


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


def _build_transcriber(
    config: WhispererConfig, client: OpenAI | None, logger: ConsoleLogger
) -> Transcriber:
    if config.use_local_backend:
        from whisperer.transcription.local_transcriber import LocalWhisperTranscriber

        logger.info("backend", f"loading local Whisper model '{config.local_model_size}'")
        return LocalWhisperTranscriber(config.local_model_size, config.transcription_prompt)

    from whisperer.transcription.openai_transcriber import OpenAITranscriber

    if client is None:
        logger.error(
            "startup",
            "No OpenAI API key found. Set OPENAI_API_KEY, or add it to .env "
            "or openai_api_key.txt in the directory you run whisperer from.",
        )
        sys.exit(1)
    return OpenAITranscriber(client, config.transcription_model, config.transcription_prompt)


def main() -> None:
    arguments = _parse_arguments()
    logger = ConsoleLogger()
    if CONFIG_FILE.exists():
        logger.info("config", f"loaded {CONFIG_FILE.resolve()}")
    else:
        logger.info("config", f"no {CONFIG_FILE} found; using built-in defaults")
    config = WhispererConfig.load()
    if arguments.local:
        config = replace(config, use_local_backend=True)

    api_key = resolve_openai_api_key()
    client = OpenAI(api_key=api_key) if api_key else None
    translator = (
        TranslationManager(
            client,
            config.translation_model,
            config.translation_system_prompt,
            reasoning_effort=config.rewrite_reasoning_effort,
        )
        if client
        else None
    )

    app = WhispererApp(
        config=config,
        recorder=AudioRecorder(
            config.sample_rate,
            on_status=lambda status: logger.warn("audio", status),
        ),
        transcriber=_build_transcriber(config, client, logger),
        translator=translator,
        output=OutputManager(),
        logger=logger,
    )
    app.run()


if __name__ == "__main__":
    main()
