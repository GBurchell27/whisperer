"""Global hotkey handling: a hold-to-record key and a tap-to-translate key."""

from __future__ import annotations

from collections.abc import Callable

from pynput.keyboard import Key, KeyCode, Listener


def parse_key(key_name: str) -> Key | KeyCode:
    """Turn a config string like 'ctrl_r' or 't' into a pynput key."""
    if len(key_name) == 1:
        return KeyCode.from_char(key_name)
    try:
        return Key[key_name]
    except KeyError as error:
        raise ValueError(f"Unknown key name: {key_name!r}") from error


class HotkeyListener:
    """Maps raw keyboard events to record-start/stop and translate callbacks."""

    def __init__(
        self,
        record_key: Key | KeyCode,
        translate_key: Key | KeyCode,
        on_record_start: Callable[[], None],
        on_record_stop: Callable[[], None],
        on_translate_tap: Callable[[], None],
    ) -> None:
        self._record_key = record_key
        self._translate_key = translate_key
        self._on_record_start = on_record_start
        self._on_record_stop = on_record_stop
        self._on_translate_tap = on_translate_tap
        self._record_key_down = False

    def run(self) -> None:
        """Block forever, dispatching hotkey events. Ctrl+C in the console exits."""
        with Listener(on_press=self._on_press, on_release=self._on_release) as listener:
            listener.join()

    def _on_press(self, key: Key | KeyCode | None) -> None:
        if key == self._record_key and not self._record_key_down:
            self._record_key_down = True
            self._on_record_start()
        elif key == self._translate_key and self._record_key_down:
            self._on_translate_tap()

    def _on_release(self, key: Key | KeyCode | None) -> None:
        if key == self._record_key and self._record_key_down:
            self._record_key_down = False
            self._on_record_stop()
