"""Timestamped, thread-safe lines for the Whisperer terminal."""

from __future__ import annotations

import sys
import threading
from datetime import datetime

_RESET = "\033[0m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_LEVEL_COLORS = {"WARN": _YELLOW, "ERROR": _RED}
_EVENT_WIDTH = 12
_TEXT_PREFIX = "             | "


def _disable_quick_edit() -> None:
    """Avoid console text selection pausing logging before a transcript is pasted."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL
    handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        # ENABLE_EXTENDED_FLAGS is required when clearing ENABLE_QUICK_EDIT_MODE.
        kernel32.SetConsoleMode(handle, (mode.value | 0x0080) & ~0x0040)


class ConsoleLogger:
    """Writes aligned event lines to stdout; locks so background work cannot interleave."""

    def __init__(self, stream=sys.stdout) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._use_color = stream.isatty()
        self._configure_stream(stream)
        if stream.isatty():
            _disable_quick_edit()

    def _configure_stream(self, stream) -> None:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            return
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    def info(self, event: str, detail: str = "") -> None:
        self._write("INFO", event, detail)

    def warn(self, event: str, detail: str = "") -> None:
        self._write("WARN", event, detail)

    def error(self, event: str, detail: str = "") -> None:
        self._write("ERROR", event, detail)

    def text(self, body: str) -> None:
        """Print a multi-line payload under the previous event."""
        if not body:
            return
        with self._lock:
            for line in body.splitlines():
                print(f"{_TEXT_PREFIX}{line}", file=self._stream, flush=True)

    def blank(self) -> None:
        with self._lock:
            print(file=self._stream, flush=True)

    def _write(self, level: str, event: str, detail: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        event_col = event.ljust(_EVENT_WIDTH)
        suffix = f"  {detail}" if detail else ""
        if level == "INFO":
            line = f"[{timestamp}] {event_col}{suffix}"
        else:
            line = f"[{timestamp}] {self._colorize(level)} {event_col}{suffix}"
        with self._lock:
            print(line, file=self._stream, flush=True)

    def _colorize(self, level: str) -> str:
        if not self._use_color:
            return level
        color = _LEVEL_COLORS.get(level, "")
        return f"{color}{level}{_RESET}" if color else level
