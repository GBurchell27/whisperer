"""Global hotkey handling: toggle recording and optional taps while recording."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping

from pynput.keyboard import HotKey, Key, KeyCode, Listener


def parse_key(key_name: str) -> Key | KeyCode:
    """Turn a config string like 'ctrl_r' or 't' into a pynput key."""
    if len(key_name) == 1:
        return KeyCode.from_char(key_name)
    try:
        return Key[key_name]
    except KeyError as error:
        raise ValueError(f"Unknown key name: {key_name!r}") from error


def keys_match(pressed: Key | KeyCode | None, target: Key | KeyCode) -> bool:
    """True if the event is the target key, including Ctrl+letter variants."""
    if pressed is None:
        return False
    if pressed == target:
        return True
    return _chars_match(pressed, target) or _virtual_keys_match(pressed, target)


def virtual_key_code(key: Key | KeyCode) -> int | None:
    """Windows virtual-key code for a pynput key, if one can be derived."""
    vk = getattr(key, "vk", None)
    if vk is not None:
        return vk
    char = getattr(key, "char", None)
    if char and len(char) == 1 and char.isalpha():
        return ord(char.upper())
    value = getattr(key, "value", None)
    return getattr(value, "vk", None)


def _chars_match(pressed: Key | KeyCode, target: Key | KeyCode) -> bool:
    pressed_char = getattr(pressed, "char", None)
    target_char = getattr(target, "char", None)
    if not pressed_char or not target_char:
        return False
    if pressed_char.lower() == target_char.lower():
        return True
    return _is_ctrl_letter(pressed_char, target_char)


def _is_ctrl_letter(pressed_char: str, target_char: str) -> bool:
    if len(pressed_char) != 1 or len(target_char) != 1 or not target_char.isalpha():
        return False
    return ord(pressed_char) == (ord(target_char.lower()) - ord("a") + 1)


def _virtual_keys_match(pressed: Key | KeyCode, target: Key | KeyCode) -> bool:
    pressed_vk = getattr(pressed, "vk", None)
    if pressed_vk is None:
        return False
    target_vk = virtual_key_code(target)
    return target_vk is not None and pressed_vk == target_vk


class HotkeyListener:
    """Maps raw keyboard events to record-start/stop and while-recording taps."""

    def __init__(
        self,
        record_key: str,
        on_record_start: Callable[[], None],
        on_record_stop: Callable[[], None],
        recording_taps: Mapping[Key | KeyCode, Callable[[], None]] | None = None,
    ) -> None:
        record_keys = (
            HotKey.parse(record_key) if "+" in record_key else [parse_key(record_key)]
        )
        self._record_hotkey = HotKey(
            [self._normalize_key(key) for key in record_keys], self._toggle_recording
        )
        self._on_record_start = on_record_start
        self._on_record_stop = on_record_stop
        self._recording_taps = dict(recording_taps or {})
        self._recording = False
        self._listener: Listener | None = None
        self._suppressed_vks = {
            vk
            for key in self._recording_taps
            if (vk := virtual_key_code(key)) is not None
        }

    def run(self) -> None:
        """Block forever, dispatching hotkey events. Ctrl+C in the console exits."""
        self.start()
        try:
            self._listener.join()
        finally:
            self.stop()

    def start(self) -> None:
        """Start hotkeys without blocking the main-thread indicator loop."""
        listener_kwargs: dict = {
            "on_press": self._on_press,
            "on_release": self._on_release,
        }
        if sys.platform == "win32":
            listener_kwargs["win32_event_filter"] = self._win32_event_filter
        self._listener = Listener(**listener_kwargs)
        self._listener.start()
        self._listener.wait()

    def check(self) -> None:
        """Surface callback failures to the UI instead of leaving a stale bubble."""
        if self._listener is not None and not self._listener.is_alive():
            self._listener.join()  # re-raises exceptions raised by a callback
            raise RuntimeError("The global hotkey listener stopped.")

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener.join()

    def _on_press(self, key: Key | KeyCode | None) -> None:
        if key is None:
            return
        self._record_hotkey.press(self._normalize_key(key))
        if self._recording:
            for tap_key, callback in self._recording_taps.items():
                if keys_match(key, tap_key):
                    callback()
                    return

    def _on_release(self, key: Key | KeyCode | None) -> None:
        if key is not None:
            self._record_hotkey.release(self._normalize_key(key))

    def _toggle_recording(self) -> None:
        if self._recording:
            self._on_record_stop()
            self._recording = False
        else:
            self._on_record_start()
            self._recording = True

    @staticmethod
    def _normalize_key(key: Key | KeyCode) -> Key | KeyCode:
        for modifier, variants in (
            (Key.ctrl, (Key.ctrl, Key.ctrl_l, Key.ctrl_r)),
            (Key.alt, (Key.alt, Key.alt_l, Key.alt_r)),
            (Key.shift, (Key.shift, Key.shift_l, Key.shift_r)),
            (Key.cmd, (Key.cmd, Key.cmd_l, Key.cmd_r)),
        ):
            if key in variants:
                return modifier
        if isinstance(key, KeyCode):
            # Windows can report Ctrl+M as '\r' (or with no character).
            # The virtual key still identifies the physical letter key.
            if key.vk is not None and 65 <= key.vk <= 90:
                return KeyCode.from_char(chr(key.vk).lower())
            if key.char is not None:
                return KeyCode.from_char(key.char.lower())
        if isinstance(key, Key):
            return KeyCode.from_vk(key.value.vk)
        return key

    def _win32_event_filter(self, _msg, data) -> bool:
        if self._recording and data.vkCode in self._suppressed_vks:
            if self._listener is not None:
                self._listener.suppress_event()
        return True
