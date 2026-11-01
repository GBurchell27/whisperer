import unittest
from unittest.mock import Mock, patch

import numpy as np

from whisperer.audio_recorder import AudioRecorder


class AudioRecorderTests(unittest.TestCase):
    def setUp(self):
        self.recorder = AudioRecorder(16000)
        self.stream = Mock()
        self.input_stream = patch(
            "whisperer.audio_recorder.sd.InputStream", return_value=self.stream
        )
        self.input_stream.start()
        self.addCleanup(self.input_stream.stop)

    def test_stop_returns_audio_and_releases_captured_buffers(self):
        self.recorder.start()
        chunk = np.array([[0.1], [0.2]], dtype=np.float32)
        self.recorder._on_audio_chunk(chunk, 2, None, None)
        chunk[:] = 0  # Capture must retain its own copy.
        audio = self.recorder.stop()
        np.testing.assert_array_equal(audio, np.array([0.1, 0.2], dtype=np.float32))
        self.assertFalse(self.recorder.is_recording)
        self.assertEqual(self.recorder._chunks, [])
        self.stream.stop.assert_called_once_with()
        self.stream.close.assert_called_once_with()

    def test_failed_start_closes_stream_and_allows_another_recording(self):
        self.stream.start.side_effect = RuntimeError("Device unavailable")
        with self.assertRaisesRegex(RuntimeError, "Device unavailable"):
            self.recorder.start()
        self.stream.close.assert_called_once_with()
        self.assertFalse(self.recorder.is_recording)
        self.stream.start.side_effect = None
        self.recorder.start()
        self.assertTrue(self.recorder.is_recording)
        self.recorder.stop()

    def test_stop_error_still_closes_stream_and_releases_audio(self):
        self.recorder.start()
        self.recorder._on_audio_chunk(np.ones((2, 1)), 2, None, None)
        self.stream.stop.side_effect = RuntimeError("Device disconnected")
        with self.assertRaisesRegex(RuntimeError, "Device disconnected"):
            self.recorder.stop()
        self.stream.close.assert_called_once_with()
        self.assertFalse(self.recorder.is_recording)
        self.assertEqual(self.recorder._chunks, [])
        self.stream.stop.side_effect = None
        self.recorder.start()
        self.assertEqual(self.recorder.stop().size, 0)

    def test_close_error_does_not_leave_stale_recording_state_or_audio(self):
        self.recorder.start()
        self.recorder._on_audio_chunk(np.ones((2, 1)), 2, None, None)
        self.stream.close.side_effect = RuntimeError("Close failed")
        with self.assertRaisesRegex(RuntimeError, "Close failed"):
            self.recorder.stop()
        self.assertFalse(self.recorder.is_recording)
        self.assertEqual(self.recorder._chunks, [])

    def test_repeated_recordings_never_include_previous_audio(self):
        for value in range(100):
            self.recorder.start()
            self.recorder.start()  # Idempotent while already recording.
            self.recorder._on_audio_chunk(np.full((2, 1), value), 2, None, None)
            np.testing.assert_array_equal(self.recorder.stop(), [value, value])
            self.assertEqual(self.recorder._chunks, [])
            self.assertEqual(self.recorder.stop().size, 0)
        self.assertEqual(self.stream.start.call_count, 100)
        self.assertEqual(self.stream.close.call_count, 100)


if __name__ == "__main__":
    unittest.main()
