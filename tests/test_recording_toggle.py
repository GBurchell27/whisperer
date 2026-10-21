import unittest
from unittest.mock import Mock, patch

import numpy as np
from pynput.keyboard import Key, KeyCode

from whisperer.app import WhispererApp
from whisperer.config import WhispererConfig
from whisperer.hotkey_listener import HotkeyListener


class RecordingToggleTests(unittest.TestCase):
    def test_second_press_stops_microphone_and_processes_recording(self):
        for mode in (None, "translate", "email"):
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
                        KeyCode.from_char("e"): app._request_email,
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
                        tap_key = (
                            Key.shift_r if mode == "translate" else KeyCode.from_char("e")
                        )
                        listener._on_press(tap_key)
                        listener._on_release(tap_key)

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
                    prompt = (
                        config.translation_system_prompt
                        if mode == "translate"
                        else config.email_system_prompt
                    )
                    translator.rewrite.assert_called_once_with("Hello.", prompt)
                else:
                    translator.rewrite.assert_not_called()
                output.paste.assert_called_once_with(
                    "Rewritten." if mode else "Hello.", keep_on_clipboard=False
                )


if __name__ == "__main__":
    unittest.main()
