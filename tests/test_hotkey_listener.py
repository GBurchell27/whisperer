import sys
import unittest
from unittest.mock import Mock, patch

from pynput.keyboard import Key, KeyCode

from whisperer.hotkey_listener import HotkeyListener, _SelectiveListener


class HotkeyListenerTests(unittest.TestCase):
    def setUp(self):
        self.start = Mock()
        self.stop = Mock()
        self.translate = Mock()
        self.listener = HotkeyListener(
            record_key="<ctrl>+m",
            on_record_start=self.start,
            on_record_stop=self.stop,
            recording_taps={Key.shift_r: self.translate},
        )
        self.m = KeyCode.from_char("m")

    def press_shortcut(self, ctrl=Key.ctrl_l):
        self.listener._on_press(ctrl)
        self.listener._on_press(self.m)

    def release_shortcut(self, ctrl=Key.ctrl_l):
        self.listener._on_release(self.m)
        self.listener._on_release(ctrl)

    def test_release_keeps_recording_until_next_shortcut_press(self):
        self.press_shortcut()
        self.release_shortcut()
        self.start.assert_called_once_with()
        self.stop.assert_not_called()
        self.press_shortcut()
        self.stop.assert_called_once_with()
        self.release_shortcut()
        self.press_shortcut()
        self.assertEqual(self.start.call_count, 2)

    def test_right_control_also_works(self):
        self.press_shortcut(Key.ctrl_r)
        self.release_shortcut(Key.ctrl_r)
        self.press_shortcut(Key.ctrl_r)
        self.start.assert_called_once_with()
        self.stop.assert_called_once_with()

    def test_individual_keys_do_not_start_recording(self):
        for key in (Key.ctrl_r, Key.ctrl_l, self.m):
            self.listener._on_press(key)
            self.listener._on_release(key)
        self.start.assert_not_called()

    def test_key_repeat_does_not_toggle_repeatedly(self):
        self.press_shortcut()
        for _ in range(10):
            self.listener._on_press(Key.ctrl_l)
            self.listener._on_press(self.m)
        self.start.assert_called_once_with()
        self.stop.assert_not_called()

    def test_m_can_be_pressed_again_while_control_stays_down(self):
        self.press_shortcut()
        self.listener._on_release(self.m)
        self.listener._on_press(self.m)
        self.stop.assert_called_once_with()

    def test_windows_control_character_and_virtual_key_events(self):
        self.listener._on_press(Key.ctrl_l)
        self.listener._on_press(KeyCode.from_char("\r", vk=77))
        self.listener._on_release(KeyCode.from_vk(77))
        self.listener._on_press(KeyCode.from_char("M", vk=77))
        self.start.assert_called_once_with()
        self.stop.assert_called_once_with()

    def test_translation_is_available_after_releasing_shortcut(self):
        self.listener._on_press(Key.shift_r)
        self.translate.assert_not_called()
        self.press_shortcut()
        self.release_shortcut()
        self.listener._on_press(Key.shift_r)
        self.translate.assert_called_once_with()
        self.press_shortcut()
        self.release_shortcut()
        self.listener._on_press(Key.shift_r)
        self.translate.assert_called_once_with()

    def test_unknown_events_are_ignored(self):
        self.listener._on_press(None)
        self.listener._on_release(None)
        self.start.assert_not_called()

    def test_recording_tap_suppression_remains_active_after_key_release(self):
        self.listener._listener = Mock()
        event = Mock(vkCode=Key.shift_r.value.vk)
        self.listener._win32_event_filter(0, event)
        self.assertFalse(self.listener._listener.suppress_current_event)
        self.press_shortcut()
        self.release_shortcut()
        self.listener._win32_event_filter(0, event)
        self.assertTrue(self.listener._listener.suppress_current_event)
        self.press_shortcut()
        self.release_shortcut()
        self.listener._win32_event_filter(0, event)
        self.assertFalse(self.listener._listener.suppress_current_event)

    def test_custom_shortcut_modifiers_and_special_keys(self):
        for shortcut, keys in (
            ("<alt>+r", (Key.alt_r, KeyCode.from_char("r"))),
            ("<ctrl>+<f13>", (Key.ctrl_r, Key.f13)),
            ("f13", (Key.f13,)),
        ):
            with self.subTest(shortcut=shortcut):
                start = Mock()
                stop = Mock()
                listener = HotkeyListener(
                    record_key=shortcut,
                    on_record_start=start,
                    on_record_stop=stop,
                    recording_taps={Key.shift_r: self.translate},
                )
                for key in keys:
                    listener._on_press(key)
                for key in reversed(keys):
                    listener._on_release(key)
                start.assert_called_once_with()
                stop.assert_not_called()
                for key in keys:
                    listener._on_press(key)
                stop.assert_called_once_with()

    def test_background_listener_starts_and_stops_cleanly(self):
        with patch("whisperer.hotkey_listener._SelectiveListener") as listener:
            self.listener.start()
            listener.return_value.start.assert_called_once_with()
            listener.return_value.wait.assert_called_once_with()
            self.listener.stop()
            listener.return_value.stop.assert_called_once_with()
            listener.return_value.join.assert_called_once_with()

    def test_listener_failure_is_reported_to_indicator_loop(self):
        self.listener._listener = Mock()
        self.listener._listener.is_alive.return_value = False
        self.listener._listener.join.side_effect = RuntimeError("Callback failed")
        with self.assertRaisesRegex(RuntimeError, "Callback failed"):
            self.listener.check()

    def test_record_shortcut_is_suppressed_but_plain_m_is_not(self):
        for ctrl_vk in (0xA2, 0xA3):
            with self.subTest(ctrl_vk=ctrl_vk):
                self.listener._listener = Mock()
                self.listener._win32_event_filter(0x100, Mock(vkCode=77))
                self.assertFalse(self.listener._listener.suppress_current_event)
                self.listener._win32_event_filter(0x101, Mock(vkCode=77))
                self.listener._win32_event_filter(0x100, Mock(vkCode=ctrl_vk))
                self.assertFalse(self.listener._listener.suppress_current_event)
                self.listener._win32_event_filter(0x100, Mock(vkCode=77))
                self.assertTrue(self.listener._listener.suppress_current_event)
                # Releasing Ctrl first must still suppress M's paired key-up.
                self.listener._win32_event_filter(0x101, Mock(vkCode=ctrl_vk))
                self.listener._win32_event_filter(0x101, Mock(vkCode=77))
                self.assertTrue(self.listener._listener.suppress_current_event)
                self.listener._win32_event_filter(0x100, Mock(vkCode=77))
                self.assertFalse(self.listener._listener.suppress_current_event)
                self.listener._win32_event_filter(0x101, Mock(vkCode=77))

    @unittest.skipUnless(sys.platform == "win32", "Windows pynput dispatch ordering")
    def test_suppression_happens_after_pynput_queues_its_own_callback(self):
        # This exercises the installed pynput handler: suppressing in its filter
        # would abort before _message_loop.post and silently break Ctrl+M.
        listener = _SelectiveListener()
        listener._message_loop = Mock()
        events = []
        listener._message_loop.post.side_effect = lambda *args: events.append("queued")
        listener.suppress_event = lambda: events.append("suppressed")
        listener.suppress_current_event = True
        with patch.object(listener, "_convert", return_value=(0x100, 77)):
            listener._handler(0, 0x100, None)
        self.assertEqual(events, ["queued", "suppressed"])


if __name__ == "__main__":
    unittest.main()
