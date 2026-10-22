import threading
import unittest
from unittest.mock import Mock, call, patch

import pyperclip
from pynput.keyboard import Key

from whisperer.output_manager import OutputManager, PASTE_SETTLE_SECONDS


class OutputManagerTests(unittest.TestCase):
    def setUp(self):
        keyboard_patch = patch("whisperer.output_manager.Controller")
        self.keyboard = keyboard_patch.start().return_value
        self.addCleanup(keyboard_patch.stop)
        self.output = OutputManager()
        self.output._get_key_state = None

    def test_paste_waits_for_shortcut_release_before_sending_keys(self):
        events = []
        self.output._wait_for_key_release = lambda: events.append("released")
        self.output._press_paste_shortcut = lambda: events.append("paste")
        with patch("whisperer.output_manager.pyperclip.copy", side_effect=lambda _: events.append("copy")), patch(
            "whisperer.output_manager.pyperclip.paste", return_value="previous"
        ), patch("whisperer.output_manager.time.sleep"):
            self.output.paste("hello", keep_on_clipboard=True)
        self.assertEqual(events, ["copy", "released", "paste"])

    def test_waits_while_a_modifier_or_record_key_is_held(self):
        held = {0x11, 77}
        self.output._get_key_state = lambda vk: 0x8000 if vk in held else 0
        with patch("whisperer.output_manager.time.sleep", side_effect=lambda _: held.clear()) as sleep:
            self.output._wait_for_key_release()
        sleep.assert_called_once_with(0.02)

    def test_stuck_modifier_keeps_transcript_for_manual_paste(self):
        self.output._get_key_state = lambda _: 0x8000
        with patch("whisperer.output_manager.time.monotonic", side_effect=[0, 4]), patch(
            "whisperer.output_manager.pyperclip.copy"
        ) as copy, patch("whisperer.output_manager.pyperclip.paste", return_value="previous"), patch(
            "whisperer.output_manager.time.sleep"
        ):
            with self.assertRaisesRegex(RuntimeError, "Transcript kept on clipboard"):
                self.output.paste("hello")
        copy.assert_called_once_with("hello")
        self.keyboard.press.assert_not_called()

    def test_restores_clipboard_after_target_has_time_to_read_it(self):
        with patch("whisperer.output_manager.pyperclip.copy") as copy, patch(
            "whisperer.output_manager.pyperclip.paste", side_effect=["previous", "hello"]
        ), patch("whisperer.output_manager.time.sleep") as sleep:
            self.output.paste("hello")
        self.assertEqual(copy.call_args_list, [call("hello"), call("previous")])
        self.assertIn(call(PASTE_SETTLE_SECONDS), sleep.call_args_list)

    def test_does_not_restore_over_something_user_just_copied(self):
        with patch("whisperer.output_manager.pyperclip.copy") as copy, patch(
            "whisperer.output_manager.pyperclip.paste", side_effect=["previous", "new user copy"]
        ), patch("whisperer.output_manager.time.sleep"):
            self.output.paste("hello")
        copy.assert_called_once_with("hello")

    def test_retries_temporary_clipboard_contention(self):
        with patch("whisperer.output_manager.pyperclip.copy", side_effect=[
            pyperclip.PyperclipException("busy"), None
        ]) as copy, patch("whisperer.output_manager.time.sleep"):
            self.output._copy_with_retry("hello")
        self.assertEqual(copy.call_args_list, [call("hello"), call("hello")])

    def test_ctrl_is_released_even_if_injecting_v_fails(self):
        self.keyboard.press.side_effect = [None, RuntimeError("Cannot inject")]
        with self.assertRaises(RuntimeError):
            self.output._press_paste_shortcut()
        self.keyboard.release.assert_called_once_with(Key.ctrl)

    def test_overlapping_pastes_cannot_interleave_clipboard_transactions(self):
        clipboard = {"text": "previous"}
        first_pasting = threading.Event()
        finish_first = threading.Event()
        second_started = threading.Event()
        copies = []
        pasted = []
        errors = []

        def copy(text):
            copies.append(text)
            clipboard["text"] = text

        def send_paste():
            pasted.append(clipboard["text"])
            if len(pasted) == 1:
                first_pasting.set()
                if not finish_first.wait(2):
                    raise RuntimeError("Test paste was not released")

        def paste(text):
            try:
                if text == "second":
                    second_started.set()
                self.output.paste(text)
            except Exception as error:
                errors.append(error)

        self.output._press_paste_shortcut = send_paste
        with patch("whisperer.output_manager.pyperclip.copy", side_effect=copy), patch(
            "whisperer.output_manager.pyperclip.paste", side_effect=lambda: clipboard["text"]
        ), patch("whisperer.output_manager.time.sleep"):
            first = threading.Thread(target=paste, args=("first",))
            second = threading.Thread(target=paste, args=("second",))
            first.start()
            try:
                self.assertTrue(first_pasting.wait(2))
                second.start()
                self.assertTrue(second_started.wait(2))
                self.assertEqual(copies, ["first"])
            finally:
                finish_first.set()
                first.join(2)
                if second.ident is not None:
                    second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(pasted, ["first", "second"])
        self.assertEqual(clipboard["text"], "previous")


if __name__ == "__main__":
    unittest.main()
