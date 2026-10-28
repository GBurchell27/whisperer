import ctypes
import sys
import unittest
from unittest.mock import Mock, patch

from whisperer.windows_input import INPUT_TAG, _Input, send_paste_shortcut


@unittest.skipUnless(sys.platform == "win32", "Windows SendInput layout and errors")
class WindowsInputTests(unittest.TestCase):
    def test_single_checked_batch_contains_only_ctrl_v_and_is_tagged(self):
        user32 = Mock()
        user32.SendInput.return_value = 4
        with patch.object(ctypes, "WinDLL", return_value=user32):
            send_paste_shortcut()
        count, inputs, size = user32.SendInput.call_args.args
        self.assertEqual(count, 4)
        self.assertEqual(size, 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual([item.value.ki.wVk for item in inputs], [17, 86, 86, 17])
        self.assertEqual([item.value.ki.dwFlags for item in inputs], [0, 0, 2, 2])
        self.assertTrue(all(item.value.ki.dwExtraInfo == INPUT_TAG for item in inputs))

    def test_rejected_input_is_an_error_instead_of_a_false_success(self):
        user32 = Mock()
        user32.SendInput.return_value = 0
        with patch.object(ctypes, "WinDLL", return_value=user32):
            with self.assertRaisesRegex(RuntimeError, "0/4.*Transcript kept"):
                send_paste_shortcut()
        self.assertEqual(user32.SendInput.call_count, 1)

    def test_partial_input_releases_keys_and_leaves_transcript_for_recovery(self):
        user32 = Mock()
        user32.SendInput.side_effect = [2, 2]
        with patch.object(ctypes, "WinDLL", return_value=user32):
            with self.assertRaisesRegex(RuntimeError, "2/4"):
                send_paste_shortcut()
        count, releases, _ = user32.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertTrue(all(item.value.ki.dwFlags == 2 for item in releases))

    def test_rejected_cleanup_names_keys_that_may_need_manual_release(self):
        user32 = Mock()
        user32.SendInput.side_effect = [2, 0]
        with patch.object(ctypes, "WinDLL", return_value=user32):
            with self.assertRaisesRegex(RuntimeError, "Key release failed.*tap and release V and Ctrl"):
                send_paste_shortcut()


if __name__ == "__main__":
    unittest.main()
