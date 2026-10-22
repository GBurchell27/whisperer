"""A movable, non-activating dictation indicator; Tk stays on the main thread."""

from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable


class DictationStatus:
    """Thread-safe status shared by the hotkeys, workers, and UI.

    Recording takes priority over unfinished transcriptions. A worker finishing
    must never make the indicator say the microphone is off while it is on.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recording = False
        self._pending = 0
        self._error_until = 0.0

    def set_recording(self, recording: bool) -> None:
        with self._lock:
            self._recording = recording
            if recording:
                self._error_until = 0.0

    def processing_started(self) -> None:
        with self._lock:
            self._pending += 1

    def processing_finished(self, *, failed: bool = False) -> None:
        with self._lock:
            self._pending = max(0, self._pending - 1)
            if failed:
                self._error_until = time.monotonic() + 5.0

    @property
    def state(self) -> str:
        with self._lock:
            if self._recording:
                return "recording"
            if self._pending:
                return "processing"
            if time.monotonic() < self._error_until:
                return "error"
            return "idle"


class StatusBubble:
    """Small always-on-top bubble. Drag anywhere except the quit button."""

    WIDTH = 224
    HEIGHT = 80
    BACKGROUND = "#111827"
    TRANSPARENT = "#010203"

    def __init__(self, status: DictationStatus, shortcut: str) -> None:
        # Import lazily so --no-overlay also works on Python builds without Tk.
        import tkinter as tk

        self._status = status
        self._shortcut = shortcut.replace("<", "").replace(">", "").upper()
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("Whisperer dictation")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.configure(bg=self.TRANSPARENT)
        self._root.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self._canvas = tk.Canvas(
            self._root, width=self.WIDTH, height=self.HEIGHT,
            bg=self.TRANSPARENT, highlightthickness=0, cursor="fleur",
        )
        self._canvas.pack()
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._closed = False
        self._last_state: str | None = None
        self._native = None
        self._hwnd = None
        self._x = self._y = 0
        self._root.update_idletasks()
        if sys.platform == "win32":
            self._root.attributes("-transparentcolor", self.TRANSPARENT)
            self._configure_windows()
        else:
            self._move(
                self._root.winfo_screenwidth() - self.WIDTH - 24,
                self._root.winfo_screenheight() - self.HEIGHT - 72,
            )
        self._canvas.bind("<ButtonPress-1>", self._begin_drag)
        self._canvas.bind("<B1-Motion>", self._drag)
        self._canvas.bind("<ButtonRelease-1>", self._end_drag)
        self._canvas.tag_bind("quit", "<Enter>", lambda _: self._canvas.configure(cursor="hand2"))
        self._canvas.tag_bind("quit", "<Leave>", lambda _: self._canvas.configure(cursor="fleur"))
        self._root.protocol("WM_DELETE_WINDOW", self.close)

    def _configure_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        self._hwnd = user32.GetParent(self._root.winfo_id()) or self._root.winfo_id()
        # WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW: never steal the paste target,
        # and don't add a taskbar/Alt+Tab entry for this small utility window.
        style = get_style(self._hwnd, -20)
        set_style(self._hwnd, -20, (style | 0x08000000 | 0x00000080) & ~0x00040000)
        self._native = user32
        work_area = wintypes.RECT()
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
            self._move(work_area.right - self.WIDTH - 24, work_area.bottom - self.HEIGHT - 24)
        else:
            self._move(
                self._root.winfo_screenwidth() - self.WIDTH - 24,
                self._root.winfo_screenheight() - self.HEIGHT - 72,
            )

    def _move(self, x: int, y: int) -> None:
        self._x, self._y = x, y
        if self._native is not None:
            # Native coordinates support monitors left/above the primary one.
            # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE.
            self._native.SetWindowPos(self._hwnd, None, x, y, 0, 0, 0x0015)
        else:
            self._root.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _begin_drag(self, event) -> None:
        if self._canvas.find_withtag("current") and "quit" in self._canvas.gettags("current"):
            self.close()
            return
        self._drag_origin = (event.x_root, event.y_root, self._x, self._y)

    def _drag(self, event) -> None:
        if self._drag_origin is not None:
            mouse_x, mouse_y, x, y = self._drag_origin
            self._move(x + event.x_root - mouse_x, y + event.y_root - mouse_y)

    def _end_drag(self, _event) -> None:
        self._drag_origin = None

    def run(self, check_listener: Callable[[], None]) -> None:
        """Run Tk on the main thread, checking hotkey health every frame."""
        self._check_listener = check_listener
        self._failure: Exception | None = None
        self._draw()
        if self._native is not None:
            # SW_SHOWNOACTIVATE; deiconify() alone may focus a new Tk window.
            self._native.ShowWindow(self._hwnd, 4)
        else:
            self._root.deiconify()
        self._root.after(40, self._tick)
        try:
            self._root.mainloop()
        finally:
            self.close()
        if self._failure is not None:
            raise self._failure

    def _tick(self) -> None:
        if self._closed:
            return
        try:
            self._check_listener()
        except Exception as error:
            self._failure = error
            self.close()
            return
        state = self._status.state
        if state != self._last_state or state in ("recording", "processing"):
            self._draw()
        self._root.after(40, self._tick)

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("frame")
        state = self._status.state
        self._last_state = state
        accent, title, subtitle = {
            "idle": ("#94a3b8", "Mic off", f"{self._shortcut} to dictate"),
            "recording": ("#fb7185", "Listening", f"{self._shortcut} to finish"),
            "processing": ("#fbbf24", "Processing", "Mic off / preparing text"),
            "error": ("#fbbf24", "Try again", "See console for details"),
        }[state]
        # Rounded pill drawn from circles and a rectangle, without image assets.
        for box in ((2, 2, 78, 78), (146, 2, 222, 78)):
            canvas.create_oval(*box, fill=self.BACKGROUND, outline="", tags="frame")
        canvas.create_rectangle(40, 2, 184, 78, fill=self.BACKGROUND, outline="", tags="frame")
        phase = time.monotonic() * 4.5
        radius = 27 + (3 * (1 + math.sin(phase)) if state == "recording" else 0)
        canvas.create_oval(40-radius, 40-radius, 40+radius, 40+radius,
                           fill="#3b2236" if state == "recording" else "#1e293b",
                           outline="", tags="frame")
        if state == "processing":
            canvas.create_arc(14, 14, 66, 66, start=-time.monotonic()*180 % 360,
                              extent=100, style="arc", outline=accent, width=3, tags="frame")
        # Microphone icon; the slash makes the off state readable without color.
        canvas.create_line(40, 29, 40, 40, fill=accent, width=8, capstyle="round", tags="frame")
        canvas.create_arc(30, 31, 50, 49, start=180, extent=180,
                          style="arc", outline=accent, width=2, tags="frame")
        canvas.create_line(40, 49, 40, 54, fill=accent, width=2, tags="frame")
        canvas.create_line(35, 54, 45, 54, fill=accent, width=2, tags="frame")
        if state != "recording":
            canvas.create_line(27, 26, 53, 55, fill=self.BACKGROUND, width=5, tags="frame")
            canvas.create_line(27, 26, 53, 55, fill=accent, width=2, tags="frame")
        canvas.create_text(80, 29, text=title, anchor="w", fill="#f8fafc",
                           font=("Segoe UI", 12, "bold"), tags="frame")
        canvas.create_text(80, 51, text=subtitle, anchor="w", fill="#94a3b8",
                           font=("Segoe UI", 8), tags="frame")
        # Keep the quit hit target stable while redrawing the animation.
        if not canvas.find_withtag("quit"):
            canvas.create_oval(194, 11, 214, 31, fill=self.BACKGROUND,
                               outline="", tags="quit")
            canvas.create_text(204, 20, text="\u00d7", fill="#94a3b8",
                               font=("Segoe UI", 12), tags="quit")
        canvas.tag_raise("quit")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._root.destroy()
