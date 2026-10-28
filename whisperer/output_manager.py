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
    """Pastes text at the cursor, keeping a recoverable transcript by default."""

    def __init__(self, record_key: str = "<ctrl>+m", *, restore_clipboard: bool = False) -> None:
        self._keyboard = Controller()
        self._paste_lock = threading.Lock()
        self._get_key_state = None
        self._windows_paste = None
        self._restore_clipboard = restore_clipboard
        keys = HotKey.parse(record_key) if "+" in record_key else [parse_key(record_key)]
        # Check both sides of every modifier as well as the configured shortcut.
        self._wait_vks = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0x56}
        self._wait_vks.update(vk for key in keys if (vk := virtual_key_code(key)) is not None)
        self._wait_key_names = {
            0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt",
            0x5B: "left Win", 0x5C: "right Win",
            0xA0: "left Shift", 0xA1: "right Shift",
            0xA2: "left Ctrl", 0xA3: "right Ctrl",
            0xA4: "left Alt", 0xA5: "right Alt",
            0x56: "V",
        }
        for key in keys:
            if (vk := virtual_key_code(key)) is not None:
                self._wait_key_names.setdefault(vk, str(key).strip("'"))
        if sys.platform == "win32":
            import ctypes
            from whisperer.windows_input import send_paste_shortcut

            self._windows_paste = send_paste_shortcut
            self._get_key_state = ctypes.WinDLL("user32").GetAsyncKeyState
            self._get_key_state.argtypes = [ctypes.c_int]
            self._get_key_state.restype = ctypes.c_short

    def paste(self, text: str, keep_on_clipboard: bool = False) -> str:
        # The entire clipboard transaction must be serialized, not just Ctrl+V.
        with self._paste_lock:
            restore = self._restore_clipboard and not keep_on_clipboard
            previous_clipboard = self._read_clipboard() if restore else None
            self._copy_with_retry(text)
            time.sleep(CLIPBOARD_WRITE_SETTLE_SECONDS)
            # Never synthesize Ctrl+V on top of a still-held Ctrl+M/Shift/Alt.
            # On failure, keep the transcript available for a manual paste.
            self._wait_for_key_release()
            self._press_paste_shortcut()
            if previous_clipboard is not None:
                time.sleep(PASTE_SETTLE_SECONDS)
                # Don't overwrite something the user copied in the meantime.
                if self._read_clipboard() == text:
                    self._copy_with_retry(previous_clipboard)
                    return "previous clipboard restored"
                return "clipboard changed by another app"
            return "transcript kept on clipboard"

    def _wait_for_key_release(self) -> None:
        if self._get_key_state is None:
            return
        deadline = time.monotonic() + KEY_RELEASE_TIMEOUT_SECONDS
        while True:
            held = sorted(vk for vk in self._wait_vks if self._get_key_state(vk) & 0x8000)
            if not held:
                return
            if time.monotonic() >= deadline:
                names = ", ".join(self._wait_key_names.get(vk, f"VK 0x{vk:02X}") for vk in held)
                raise RuntimeError(
                    f"Windows still reports these keys held: {names}. "
                    "Release them; if already released, tap and release them again. "
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
        if self._windows_paste is not None:
            self._windows_paste()
            return
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
