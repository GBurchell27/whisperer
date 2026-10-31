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
from whisperer.hotkey_listener import HotkeyListener
from whisperer.output_manager import OutputManager
from whisperer.status_bubble import DictationStatus, StatusBubble
from whisperer.transcription.base import Transcriber

NEW_PARAGRAPH_COMMAND = "New paragraph."


class WhispererApp:
    """Coordinates the record → transcribe → paste pipeline."""

    def __init__(
        self,
        config: WhispererConfig,
        recorder: AudioRecorder,
        transcriber: Transcriber,
        output: OutputManager,
        logger: ConsoleLogger,
    ) -> None:
        self._config = config
        self._recorder = recorder
        self._transcriber = transcriber
        self._output = output
        self._log = logger
        self._keep_next_on_clipboard = False
        self._status = DictationStatus()

    def run(self, *, show_overlay: bool = True) -> None:
        listener = HotkeyListener(
            record_key=self._config.record_key,
            on_record_start=self._start_recording,
            on_record_stop=self._stop_recording,
        )
        self._log_startup()
        bubble = None
        if show_overlay:
            try:
                bubble = StatusBubble(self._status, self._config.record_key)
            except (ImportError, RuntimeError) as error:
                self._log.warn("indicator", f"unavailable: {error}")
            except Exception as error:
                # Tk can be installed but unavailable (e.g. no desktop display).
                self._log.warn("indicator", f"could not open: {error}")
        try:
            if bubble is None:
                listener.run()
            else:
                listener.start()
                bubble.run(listener.check)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                listener.stop()
            finally:
                try:
                    self._recorder.stop()
                finally:
                    self._status.set_recording(False)
                    if bubble is not None:
                        bubble.close()

    def _log_startup(self) -> None:
        backend = "local" if self._config.use_local_backend else "openai"
        transcribe_model = (
            self._config.local_model_size
            if self._config.use_local_backend
            else self._config.transcription_model
        )
        self._log.info(
            "ready",
            f"press {self._config.record_key} to start/stop recording | Ctrl+C quits",
        )
        self._log.info("backend", f"{backend}  transcribe={transcribe_model}")

    def _start_recording(self) -> None:
        self._log.blank()
        self._recorder.start()
        self._status.set_recording(True)
        self._log.info("record", "started")

    def _stop_recording(self) -> None:
        audio = self._recorder.stop()
        self._status.set_recording(False)
        duration_seconds = len(audio) / self._config.sample_rate
        if duration_seconds < self._config.tap_duration_seconds:
            self._keep_next_on_clipboard = True
            self._log.info(
                "record",
                f"stopped  {duration_seconds:.1f}s  short recording -> keep next on clipboard",
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
        keep_on_clipboard = self._keep_next_on_clipboard
        self._keep_next_on_clipboard = False
        self._log.info("record", f"stopped  {duration_seconds:.1f}s  -> verbatim")
        worker = threading.Thread(
            target=self._process_recording,
            args=(audio, keep_on_clipboard),
            daemon=True,
        )
        self._status.processing_started()
        try:
            worker.start()
        except Exception:
            self._status.processing_finished(failed=True)
            raise

    def _process_recording(
        self, audio: np.ndarray, keep_on_clipboard: bool
    ) -> None:
        failed = False
        started = time.perf_counter()
        stage = "transcription"
        stage_started = started
        timings: dict[str, float] = {}
        try:
            transcript = self._transcribe(audio)
            timings[stage] = time.perf_counter() - stage_started
            stage = "paste"
            stage_started = time.perf_counter()
            self._paste_or_skip(transcript, keep_on_clipboard)
            timings[stage] = time.perf_counter() - stage_started
        except Exception as error:  # keep the hotkey loop alive whatever goes wrong
            failed = True
            timings[stage] = time.perf_counter() - stage_started
            self._log.error("failed", f"{stage}: {type(error).__name__}: {error}")
        finally:
            self._status.processing_finished(failed=failed)
            elapsed = time.perf_counter() - started
            breakdown = "  ".join(f"{name}={seconds:.1f}s" for name, seconds in timings.items())
            self._log.info("timing", f"total={elapsed:.1f}s  {breakdown}")

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

    def _paste_or_skip(self, transcript: str, keep_on_clipboard: bool) -> None:
        if not transcript:
            self._log.warn("paste", "skipped (empty transcript)")
            return
        result = self._output.paste(transcript, keep_on_clipboard=keep_on_clipboard)
        # Sending a shortcut doesn't prove the target app consumed the text.
        self._log.info("paste", f"shortcut sent  {result}")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="whisperer",
        description="Press Ctrl+M to start dictating; press again to transcribe and paste.",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="run without the floating microphone indicator",
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
    return OpenAITranscriber(
        client, config.transcription_model, config.transcription_prompt,
        chunk_seconds=config.transcription_chunk_seconds,
        on_progress=lambda detail: logger.info("transcribe", detail),
    )


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

    app = WhispererApp(
        config=config,
        recorder=AudioRecorder(
            config.sample_rate,
            on_status=lambda status: logger.warn("audio", status),
        ),
        transcriber=_build_transcriber(config, client, logger),
        output=OutputManager(config.record_key, restore_clipboard=config.restore_clipboard),
        logger=logger,
    )
    app.run(show_overlay=not arguments.no_overlay)


if __name__ == "__main__":
    main()
