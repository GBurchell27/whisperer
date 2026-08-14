"""Global hotkey handling: a hold-to-record key and optional taps while recording."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping

from pynput.keyboard import Key, KeyCode, Listener


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
        record_key: Key | KeyCode,
        on_record_start: Callable[[], None],
        on_record_stop: Callable[[], None],
        recording_taps: Mapping[Key | KeyCode, Callable[[], None]] | None = None,
    ) -> None:
        self._record_key = record_key
        self._on_record_start = on_record_start
        self._on_record_stop = on_record_stop
        self._recording_taps = dict(recording_taps or {})
        self._record_key_down = False
        self._listener: Listener | None = None
        self._suppressed_vks = {
            vk
            for key in self._recording_taps
            if (vk := virtual_key_code(key)) is not None
        }

    def run(self) -> None:
        """Block forever, dispatching hotkey events. Ctrl+C in the console exits."""
        listener_kwargs: dict = {
            "on_press": self._on_press,
            "on_release": self._on_release,
        }
        if sys.platform == "win32":
            listener_kwargs["win32_event_filter"] = self._win32_event_filter
        self._listener = Listener(**listener_kwargs)
        with self._listener:
            self._listener.join()

    def _on_press(self, key: Key | KeyCode | None) -> None:
        if keys_match(key, self._record_key) and not self._record_key_down:
            self._record_key_down = True
            self._on_record_start()
            return
        if not self._record_key_down:
            return
        for tap_key, callback in self._recording_taps.items():
            if keys_match(key, tap_key):
                callback()
                return

    def _on_release(self, key: Key | KeyCode | None) -> None:
        if keys_match(key, self._record_key) and self._record_key_down:
            self._record_key_down = False
            self._on_record_stop()

    def _win32_event_filter(self, _msg, data) -> bool:
        if self._record_key_down and data.vkCode in self._suppressed_vks:
            if self._listener is not None:
                self._listener.suppress_event()
        return True
