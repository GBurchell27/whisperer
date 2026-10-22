import unittest
from unittest.mock import Mock, patch

import numpy as np

from whisperer.app import WhispererApp
from whisperer.config import WhispererConfig
from whisperer.status_bubble import DictationStatus


class DictationStatusTests(unittest.TestCase):
    def test_new_recording_takes_priority_over_older_processing(self):
        status = DictationStatus()
        self.assertEqual(status.state, "idle")
        status.processing_started()
        status.set_recording(True)
        status.processing_finished()
        self.assertEqual(status.state, "recording")
        status.set_recording(False)
        self.assertEqual(status.state, "idle")

    def test_processing_stays_visible_until_all_workers_finish(self):
        status = DictationStatus()
        status.processing_started()
        status.processing_started()
        status.processing_finished()
        self.assertEqual(status.state, "processing")
        status.processing_finished()
        self.assertEqual(status.state, "idle")

    def test_failure_clears_after_timeout_or_next_recording(self):
        status = DictationStatus()
        with patch("whisperer.status_bubble.time.monotonic", return_value=10):
            status.processing_started()
            status.processing_finished(failed=True)
            self.assertEqual(status.state, "error")
        with patch("whisperer.status_bubble.time.monotonic", return_value=16):
            self.assertEqual(status.state, "idle")
        status.processing_started()
        status.processing_finished(failed=True)
        status.set_recording(True)
        self.assertEqual(status.state, "recording")
        status.set_recording(False)
        self.assertEqual(status.state, "idle")


class AppIndicatorTests(unittest.TestCase):
    def setUp(self):
        self.config = WhispererConfig()
        self.recorder = Mock()
        self.transcriber = Mock()
        self.transcriber.transcribe.return_value = "Hello."
        self.app = WhispererApp(
            self.config, self.recorder, self.transcriber, None, Mock(), Mock()
        )

    def test_recording_processing_and_finished_states(self):
        self.recorder.stop.return_value = np.zeros(self.config.sample_rate)
        self.app._start_recording()
        self.assertEqual(self.app._status.state, "recording")
        with patch("whisperer.app.threading.Thread") as worker:
            self.app._stop_recording()
            self.assertEqual(self.app._status.state, "processing")
            worker.call_args.kwargs["target"](*worker.call_args.kwargs["args"])
        self.assertEqual(self.app._status.state, "idle")

    def test_short_recordings_return_to_mic_off_without_processing(self):
        for duration in (0.1, 0.7):
            with self.subTest(duration=duration):
                self.recorder.stop.return_value = np.zeros(int(self.config.sample_rate * duration))
                self.app._start_recording()
                with patch("whisperer.app.threading.Thread") as worker:
                    self.app._stop_recording()
                    worker.assert_not_called()
                self.assertEqual(self.app._status.state, "idle")

    def test_failed_microphone_start_never_shows_listening(self):
        self.recorder.start.side_effect = RuntimeError("Microphone unavailable")
        with self.assertRaises(RuntimeError):
            self.app._start_recording()
        self.assertEqual(self.app._status.state, "idle")

    def test_transcription_error_does_not_leave_processing_animation_running(self):
        self.transcriber.transcribe.side_effect = RuntimeError("Offline")
        self.app._status.processing_started()
        self.app._process_recording(np.zeros(self.config.sample_rate), None, False)
        self.assertEqual(self.app._status.state, "error")

    def test_closing_overlay_stops_hotkeys_and_microphone(self):
        with patch("whisperer.app.HotkeyListener") as listener, patch(
            "whisperer.app.StatusBubble"
        ) as bubble:
            self.app.run()
        listener.return_value.start.assert_called_once_with()
        listener.return_value.stop.assert_called_once_with()
        self.recorder.stop.assert_called_once_with()
        bubble.return_value.close.assert_called_once_with()

    def test_no_overlay_and_unavailable_tk_preserve_console_mode(self):
        for show_overlay in (True, False):
            with self.subTest(show_overlay=show_overlay), patch(
                "whisperer.app.HotkeyListener"
            ) as listener, patch(
                "whisperer.app.StatusBubble", side_effect=ImportError("No Tk")
            ) as bubble:
                self.app.run(show_overlay=show_overlay)
                listener.return_value.run.assert_called_once_with()
                if not show_overlay:
                    bubble.assert_not_called()

    def test_indicator_closes_even_if_audio_cleanup_fails(self):
        self.recorder.stop.side_effect = RuntimeError("Audio device disconnected")
        with patch("whisperer.app.HotkeyListener"), patch(
            "whisperer.app.StatusBubble"
        ) as bubble:
            with self.assertRaises(RuntimeError):
                self.app.run()
        bubble.return_value.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
