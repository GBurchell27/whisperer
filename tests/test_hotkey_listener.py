import sys
import ctypes
import threading
from types import SimpleNamespace
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

    def test_missing_m_release_cannot_make_control_alone_toggle_recording(self):
        self.press_shortcut()
        self.listener._on_release(Key.ctrl_l)  # Deliberately miss M-up.
        self.listener._on_press(Key.ctrl_l)
        self.stop.assert_not_called()
        self.listener._on_press(self.m)
        self.stop.assert_called_once_with()

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
        event = SimpleNamespace(vkCode=Key.shift_r.value.vk, flags=0)
        self.listener._win32_event_filter(0x100, event)
        self.assertFalse(self.listener._listener.suppress_current_event)
        self.listener._win32_event_filter(0x101, event)
        self.press_shortcut()
        self.release_shortcut()
        self.listener._win32_event_filter(0x100, event)
        self.assertTrue(self.listener._listener.suppress_current_event)
        self.listener._win32_event_filter(0x101, event)
        self.assertTrue(self.listener._listener.suppress_current_event)
        self.press_shortcut()
        self.release_shortcut()
        self.listener._win32_event_filter(0x100, event)
        self.assertFalse(self.listener._listener.suppress_current_event)

    def test_shift_held_before_recording_can_release_without_sticking_in_windows(self):
        self.listener._listener = Mock()
        event = SimpleNamespace(vkCode=Key.shift_r.value.vk, flags=0)
        self.listener._win32_event_filter(0x100, event)
        self.assertFalse(self.listener._listener.suppress_current_event)
        self.press_shortcut()
        self.release_shortcut()
        # A repeated down must not change how the matching release is handled.
        self.listener._win32_event_filter(0x100, event)
        self.listener._win32_event_filter(0x101, event)
        self.assertFalse(self.listener._listener.suppress_current_event)

    def test_consumed_shift_release_stays_consumed_after_recording_stops(self):
        self.listener._listener = Mock()
        self.press_shortcut()
        self.release_shortcut()
        event = SimpleNamespace(vkCode=Key.shift_r.value.vk, flags=0)
        self.listener._win32_event_filter(0x100, event)
        self.assertTrue(self.listener._listener.suppress_current_event)
        self.press_shortcut()
        self.release_shortcut()
        self.listener._win32_event_filter(0x101, event)
        self.assertTrue(self.listener._listener.suppress_current_event)
        # The following ordinary Shift press/release reaches the target app.
        self.listener._win32_event_filter(0x100, event)
        self.assertFalse(self.listener._listener.suppress_current_event)
        self.listener._win32_event_filter(0x101, event)
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

    def test_repeated_cleanup_joins_pynput_only_once(self):
        with patch("whisperer.hotkey_listener._SelectiveListener") as native:
            # pynput 1.7 consumes its stop/error queue in join. A second join
            # on the same stopped listener would wait on that empty queue.
            native.return_value.join.side_effect = [None, AssertionError("Joined twice")]
            self.listener.run()
            self.listener.stop()  # The application's outer finally also stops.
            native.return_value.join.assert_called_once_with()

    def test_callback_failure_is_not_joined_again_during_cleanup(self):
        self.listener._listener = Mock()
        self.listener._listener.is_alive.return_value = False
        self.listener._listener.join.side_effect = [RuntimeError("Callback failed"), AssertionError("Joined twice")]
        with self.assertRaisesRegex(RuntimeError, "Callback failed"):
            self.listener.check()
        self.listener.stop()
        self.listener._listener.join.assert_called_once_with()

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

    @unittest.skipUnless(sys.platform == "win32", "Windows native hook conversion")
    def test_paste_cannot_restart_recording_even_with_a_missing_m_release(self):
        listener = _SelectiveListener(
            on_press=self.listener._on_press,
            on_release=self.listener._on_release,
            win32_event_filter=self.listener._win32_event_filter,
        )
        self.listener._listener = listener
        listener._message_loop = Mock()
        listener._message_loop.post.side_effect = lambda _msg, msg, vk: listener._process(msg, vk)
        listener.suppress_event = lambda: None

        def event(msg, vk, flags=0):
            data = listener._KBDLLHOOKSTRUCT(vkCode=vk, flags=flags)
            listener._handler(0, msg, ctypes.byref(data))

        for msg, vk in ((0x100, 0xA2), (0x100, 77), (0x101, 77), (0x101, 0xA2)):
            event(msg, vk)
        for msg, vk in ((0x100, 0xA2), (0x100, 77), (0x101, 0xA2)):
            event(msg, vk)  # Deliberately drop M-up, leaving stale hotkey state.
        self.start.assert_called_once_with()
        self.stop.assert_called_once_with()
        for msg, vk in ((0x100, 0x11), (0x100, 86), (0x101, 86), (0x101, 0x11)):
            event(msg, vk, 0x10)  # Windows SendInput flags these events as injected.
        self.start.assert_called_once_with()
        self.assertFalse(self.listener._recording)
        event(0x101, 77)
        event(0x100, 0xA2)
        event(0x100, 77)
        self.assertEqual(self.start.call_count, 2)

    def test_injected_input_is_not_suppressed_or_added_to_physical_key_state(self):
        self.listener._listener = Mock(suppress_current_event=True)
        for flags in (0x10, 0x12, 0x90):
            self.assertFalse(self.listener._win32_event_filter(
                0x100, SimpleNamespace(vkCode=77, flags=flags)
            ))
            self.assertFalse(self.listener._listener.suppress_current_event)
            self.assertEqual(self.listener._pressed_vks, set())

    def test_ctrl_remains_available_for_manual_paste_when_m_was_pressed_first(self):
        # Model what the target app sees, including the same held Ctrl being
        # reused for paste. Swallowing that Ctrl-down breaks shortcuts globally.
        for ctrl_vk in (0xA2, 0xA3):
            with self.subTest(ctrl_vk=ctrl_vk):
                listener = HotkeyListener("<ctrl>+m", Mock(), Mock())
                listener._listener = Mock()
                target_held = set()
                paste_seen = False
                for msg, vk in (
                    (0x100, 77), (0x100, ctrl_vk), (0x101, 77),
                    (0x100, 86), (0x101, 86), (0x101, ctrl_vk),
                ):
                    listener._win32_event_filter(msg, SimpleNamespace(vkCode=vk, flags=0))
                    if not listener._listener.suppress_current_event:
                        if msg == 0x100:
                            target_held.add(vk)
                            if vk == 86 and ctrl_vk in target_held:
                                paste_seen = True
                        else:
                            target_held.discard(vk)
                self.assertTrue(paste_seen)
                self.assertEqual(target_held, set())

    @unittest.skipUnless(sys.platform == "win32", "Windows native hook conversion")
    def test_unhandled_hook_code_cannot_reuse_previous_suppression(self):
        listener = _SelectiveListener()
        listener._message_loop = Mock()
        listener.suppress_event = Mock()
        listener.suppress_current_event = True
        listener._handler(-1, 0x100, None)
        listener.suppress_event.assert_not_called()
        listener._message_loop.post.assert_not_called()

    def test_slow_microphone_action_does_not_block_keyboard_and_actions_stay_ordered(self):
        started = threading.Event()
        finish_start = threading.Event()
        dispatched = threading.Event()
        stopped = threading.Event()
        actions = []

        def slow_start():
            actions.append("start")
            started.set()
            if not finish_start.wait(2):
                raise RuntimeError("Test did not release microphone start")

        def stop():
            actions.append("stop")
            stopped.set()

        self.listener._on_record_start = slow_start
        self.listener._on_record_stop = stop
        self.listener._recording_taps[Key.shift_r] = lambda: actions.append("translate")
        driver = threading.Thread(
            target=lambda: (self.press_shortcut(), self.release_shortcut(), dispatched.set()),
            daemon=True,
        )
        with patch("whisperer.hotkey_listener._SelectiveListener"):
            self.listener.start()
            driver.start()
            try:
                self.assertTrue(started.wait(1))
                self.assertTrue(dispatched.wait(1), "Microphone start blocked the keyboard listener")
                self.listener._on_press(Key.shift_r)
                self.listener._on_release(Key.shift_r)
                self.press_shortcut()
                self.release_shortcut()
                for msg, vk in ((0x100, 0xA2), (0x100, 86), (0x101, 86), (0x101, 0xA2)):
                    self.listener._win32_event_filter(msg, SimpleNamespace(vkCode=vk, flags=0))
                    self.assertFalse(self.listener._listener.suppress_current_event)
                self.assertEqual(actions, ["start"])
                finish_start.set()
                self.assertTrue(stopped.wait(1))
                self.assertEqual(actions, ["start", "translate", "stop"])
            finally:
                finish_start.set()
                driver.join(2)
                self.listener.stop()

    def test_failed_action_stops_hook_and_surfaces_original_error(self):
        failed = threading.Event()
        cause = RuntimeError("Microphone disconnected")
        self.listener._on_record_start = Mock(side_effect=cause)
        with patch("whisperer.hotkey_listener._SelectiveListener") as native:
            native.return_value.stop.side_effect = failed.set
            self.listener.start()
            try:
                self.press_shortcut()
                self.assertTrue(failed.wait(1))
                with self.assertRaisesRegex(RuntimeError, "keyboard hook stopped") as error:
                    self.listener.check()
                self.assertIs(error.exception.__cause__, cause)
            finally:
                self.listener.stop()

    def test_shutdown_discards_queued_actions_before_audio_cleanup(self):
        started = threading.Event()
        finish_start = threading.Event()
        unhooked = threading.Event()
        self.listener._on_record_start = lambda: (started.set(), finish_start.wait(2))
        with patch("whisperer.hotkey_listener._SelectiveListener") as native:
            native.return_value.stop.side_effect = unhooked.set
            self.listener.start()
            shutdown = threading.Thread(target=self.listener.stop, daemon=True)
            try:
                self.press_shortcut()
                self.release_shortcut()
                self.assertTrue(started.wait(1))
                self.press_shortcut()  # Queue stop while start is still blocked.
                shutdown.start()
                self.assertTrue(unhooked.wait(1))
            finally:
                finish_start.set()
                if shutdown.ident is not None:
                    shutdown.join(2)
                self.listener.stop()
            self.assertFalse(shutdown.is_alive())
            self.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
