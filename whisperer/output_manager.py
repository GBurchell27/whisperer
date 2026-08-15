"""Delivers transcript text to the active window via a clipboard paste."""

from __future__ import annotations

import sys
import time

import pyperclip
from pynput.keyboard import Controller, Key

# Give the target application time to read the clipboard before we restore it.
PASTE_SETTLE_SECONDS = 0.3
CLIPBOARD_WRITE_SETTLE_SECONDS = 0.05


class OutputManager:
    """Pastes text at the cursor, restoring the previous clipboard unless asked to keep it."""

    def __init__(self) -> None:
        self._keyboard = Controller()

    def paste(self, text: str, keep_on_clipboard: bool = False) -> None:
        previous_clipboard = self._read_clipboard()
        pyperclip.copy(text)
        time.sleep(CLIPBOARD_WRITE_SETTLE_SECONDS)
        self._press_paste_shortcut()
        if not keep_on_clipboard and previous_clipboard is not None:
            time.sleep(PASTE_SETTLE_SECONDS)
            pyperclip.copy(previous_clipboard)

    def _press_paste_shortcut(self) -> None:
        modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
        self._keyboard.press(modifier)
        self._keyboard.press("v")
        self._keyboard.release("v")
        self._keyboard.release(modifier)

    def _read_clipboard(self) -> str | None:
        try:
            return pyperclip.paste()
        except pyperclip.PyperclipException:
            return None
