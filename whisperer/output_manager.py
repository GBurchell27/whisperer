"""Delivers transcript text to the active window via a clipboard paste."""

from __future__ import annotations

import sys
import threading
import time

import pyperclip
from pynput.keyboard import Controller, HotKey, Key

from whisperer.hotkey_listener import parse_key, virtual_key_code

# Give the target application time to read the clipboard before we restore it.
PASTE_SETTLE_SECONDS = 0.75
CLIPBOARD_WRITE_SETTLE_SECONDS = 0.05
KEY_RELEASE_TIMEOUT_SECONDS = 3.0


class OutputManager:
    """Pastes text at the cursor, restoring the previous clipboard unless asked to keep it."""

    def __init__(self, record_key: str = "<ctrl>+m") -> None:
        self._keyboard = Controller()
        self._paste_lock = threading.Lock()
        self._get_key_state = None
        keys = HotKey.parse(record_key) if "+" in record_key else [parse_key(record_key)]
        # Check both sides of every modifier as well as the configured shortcut.
        self._wait_vks = {0x10, 0x11, 0x12, 0x5B, 0x5C}
        self._wait_vks.update(vk for key in keys if (vk := virtual_key_code(key)) is not None)
        if sys.platform == "win32":
            import ctypes

            self._get_key_state = ctypes.WinDLL("user32").GetAsyncKeyState
            self._get_key_state.argtypes = [ctypes.c_int]
            self._get_key_state.restype = ctypes.c_short

    def paste(self, text: str, keep_on_clipboard: bool = False) -> None:
        # The entire clipboard transaction must be serialized, not just Ctrl+V.
        with self._paste_lock:
            previous_clipboard = self._read_clipboard()
            self._copy_with_retry(text)
            time.sleep(CLIPBOARD_WRITE_SETTLE_SECONDS)
            # Never synthesize Ctrl+V on top of a still-held Ctrl+M/Shift/Alt.
            # On failure, keep the transcript available for a manual paste.
            self._wait_for_key_release()
            self._press_paste_shortcut()
            if not keep_on_clipboard and previous_clipboard is not None:
                time.sleep(PASTE_SETTLE_SECONDS)
                # Don't overwrite something the user copied in the meantime.
                if self._read_clipboard() == text:
                    self._copy_with_retry(previous_clipboard)

    def _wait_for_key_release(self) -> None:
        if self._get_key_state is None:
            return
        deadline = time.monotonic() + KEY_RELEASE_TIMEOUT_SECONDS
        while any(self._get_key_state(vk) & 0x8000 for vk in self._wait_vks):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Release the recording shortcut and modifier keys. "
                    "Transcript kept on clipboard; press Ctrl+V to paste it."
                )
            time.sleep(0.02)

    @staticmethod
    def _copy_with_retry(text: str) -> None:
        for attempt in range(3):
            try:
                pyperclip.copy(text)
                return
            except pyperclip.PyperclipException:
                if attempt == 2:
                    raise
                time.sleep(0.05)

    def _press_paste_shortcut(self) -> None:
        self._keyboard.press(Key.ctrl)
        try:
            self._keyboard.press("v")
            try:
                time.sleep(0.02)
            finally:
                self._keyboard.release("v")
        finally:
            self._keyboard.release(Key.ctrl)

    def _read_clipboard(self) -> str | None:
        try:
            return pyperclip.paste()
        except pyperclip.PyperclipException:
            return None
