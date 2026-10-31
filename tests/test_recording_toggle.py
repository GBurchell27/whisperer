import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from pynput.keyboard import Key, KeyCode

from whisperer.app import WhispererApp
from whisperer.config import WhispererConfig
from whisperer.hotkey_listener import HotkeyListener


class RecordingToggleTests(unittest.TestCase):
    def test_second_press_stops_microphone_and_processes_recording(self):
        for mode in (None, "translate"):
            with self.subTest(mode=mode):
                config = WhispererConfig()
                recorder = Mock()
                audio = np.zeros(config.sample_rate, dtype=np.float32)
                recorder.stop.return_value = audio
                transcriber = Mock()
                transcriber.transcribe.return_value = "Hello."
                translator = Mock()
                translator.rewrite.return_value = "Rewritten."
                output = Mock()
                app = WhispererApp(config, recorder, transcriber, translator, output, Mock())
                listener = HotkeyListener(
                    record_key=config.record_key,
                    on_record_start=app._start_recording,
                    on_record_stop=app._stop_recording,
                    recording_taps={
                        Key.shift_r: app._request_translation,
                    },
                )

                with patch("whisperer.app.threading.Thread") as worker:
                    listener._on_press(Key.ctrl_l)
                    listener._on_press(KeyCode.from_char("m"))
                    listener._on_release(KeyCode.from_char("m"))
                    listener._on_release(Key.ctrl_l)
                    recorder.start.assert_called_once_with()
                    recorder.stop.assert_not_called()
                    worker.assert_not_called()

                    if mode:
                        listener._on_press(Key.shift_r)
                        listener._on_release(Key.shift_r)

                    listener._on_press(Key.ctrl_l)
                    listener._on_press(KeyCode.from_char("m"))
                    recorder.stop.assert_called_once_with()
                    worker.return_value.start.assert_called_once_with()
                    args = worker.call_args.kwargs["args"]
                    self.assertIs(args[0], audio)
                    self.assertEqual(args[1:], (mode, False))
                    worker.call_args.kwargs["target"](*args)

                transcriber.transcribe.assert_called_once_with(audio, config.sample_rate)
                if mode:
                    translator.rewrite.assert_called_once_with(
                        "Hello.", config.translation_system_prompt
                    )
                else:
                    translator.rewrite.assert_not_called()
                output.paste.assert_called_once_with(
                    "Rewritten." if mode else "Hello.", keep_on_clipboard=False
                )

    def test_typing_e_while_recording_passes_through_and_keeps_verbatim_output(self):
        config = WhispererConfig()
        recorder = Mock()
        audio = np.zeros(config.sample_rate, dtype=np.float32)
        recorder.stop.return_value = audio
        transcriber = Mock()
        transcriber.transcribe.return_value = "Hello."
        translator = Mock()
        output = Mock()
        app = WhispererApp(config, recorder, transcriber, translator, output, Mock())

        def record_while_typing(listener):
            # Use the listener wired by app.run(), without installing a global hook.
            listener._listener = Mock()
            listener._on_press(Key.ctrl_l)
            listener._on_press(KeyCode.from_char("m"))
            listener._on_release(KeyCode.from_char("m"))
            listener._on_release(Key.ctrl_l)
            for char in ("e", "e", "E"):
                for msg in (0x100, 0x100, 0x101):  # press, auto-repeat, release
                    listener._win32_event_filter(msg, SimpleNamespace(vkCode=69, flags=0))
                    self.assertFalse(listener._listener.suppress_current_event)
                    callback = listener._on_release if msg == 0x101 else listener._on_press
                    callback(KeyCode.from_char(char))
            listener._on_press(Key.ctrl_l)
            listener._on_press(KeyCode.from_char("m"))
            args = worker.call_args.kwargs["args"]
            self.assertEqual(args[1:], (None, False))
            worker.call_args.kwargs["target"](*args)

        with patch(
            "whisperer.app.HotkeyListener.run", autospec=True, side_effect=record_while_typing
        ), patch("whisperer.app.threading.Thread") as worker:
            app.run(show_overlay=False)

        transcriber.transcribe.assert_called_once_with(audio, config.sample_rate)
        translator.rewrite.assert_not_called()
        output.paste.assert_called_once_with("Hello.", keep_on_clipboard=False)


if __name__ == "__main__":
    unittest.main()
