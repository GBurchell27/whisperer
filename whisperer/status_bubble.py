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
        self._video_phase = "idle"
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

    def set_video_recording(self, recording: bool) -> None:
        self.set_video_phase("recording" if recording else "idle")

    def set_video_phase(self, phase: str) -> None:
        with self._lock:
            self._video_phase = phase

    @property
    def video_phase(self) -> str:
        with self._lock:
            return self._video_phase

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
            if self._video_phase == "recording":
                return "video"
            if self._video_phase in ("starting", "saving"):
                return f"video_{self._video_phase}"
            if self._pending:
                return "processing"
            if time.monotonic() < self._error_until:
                return "error"
            return "idle"


class StatusBubble:
    """Always-on-top status and shortcuts, with a clickable video control."""

    WIDTH = 376
    HEIGHT = 108
    BACKGROUND = "#111827"
    TRANSPARENT = "#010203"

    def __init__(
        self, status: DictationStatus, shortcut: str,
        video_shortcut: str, on_video_toggle: Callable[[], None],
    ) -> None:
        # Import lazily so --no-overlay also works on Python builds without Tk.
        import tkinter as tk

        self._status = status
        self._shortcut = shortcut.replace("<", "").replace(">", "").upper()
        self._video_shortcut = video_shortcut.replace("<", "").replace(">", "").upper()
        self._on_video_toggle = on_video_toggle
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("Whisperer controls")
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
        self._last_video_phase: str | None = None
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
        for tag in ("quit", "video_control"):
            self._canvas.tag_bind(tag, "<Enter>", lambda _: self._canvas.configure(cursor="hand2"))
            self._canvas.tag_bind(tag, "<Leave>", lambda _: self._canvas.configure(cursor="fleur"))
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

    @property
    def root(self):
        """The main-thread Tk owner for auxiliary tool windows."""
        return self._root

    def _move(self, x: int, y: int) -> None:
        self._x, self._y = x, y
        if self._native is not None:
            # Native coordinates support monitors left/above the primary one.
            # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE.
            self._native.SetWindowPos(self._hwnd, None, x, y, 0, 0, 0x0015)
        else:
            self._root.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _begin_drag(self, event) -> None:
        tags = self._canvas.gettags("current")
        if "quit" in tags:
            self.close()
            return
        if "video_control" in tags:
            if self._status.video_phase != "saving":
                self._on_video_toggle()
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
        if (state != self._last_state or self._status.video_phase != self._last_video_phase
                or state in ("recording", "video", "processing")):
            self._draw()
        self._root.after(40, self._tick)

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("frame")
        state = self._status.state
        self._last_state = state
        video_phase = self._status.video_phase
        self._last_video_phase = video_phase
        accent, title = {
            "idle": ("#94a3b8", "Whisperer · Mic off"),
            "recording": ("#fb7185", "Listening"),
            "video": ("#fb7185", "Recording screen"),
            "video_starting": ("#fbbf24", "Starting screen recording…"),
            "video_saving": ("#fbbf24", "Saving video…"),
            "processing": ("#fbbf24", "Preparing dictation…"),
            "error": ("#fbbf24", "Dictation failed · see console"),
        }[state]
        # Rounded pill drawn from circles and a rectangle, without image assets.
        for box in ((2, 2, 106, 106), (self.WIDTH-106, 2, self.WIDTH-2, 106)):
            canvas.create_oval(*box, fill=self.BACKGROUND, outline="", tags="frame")
        canvas.create_rectangle(54, 2, self.WIDTH-54, 106,
                                fill=self.BACKGROUND, outline="", tags="frame")
        phase = time.monotonic() * 4.5
        radius = 27 + (3 * (1 + math.sin(phase)) if state in ("recording", "video") else 0)
        canvas.create_oval(40-radius, 40-radius, 40+radius, 40+radius,
                           fill="#3b2236" if state in ("recording", "video") else "#1e293b",
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
        if state == "video":
            canvas.create_oval(19, 19, 61, 61, fill="#3b2236", outline="", tags="frame")
            canvas.create_rectangle(31, 31, 49, 49, fill=accent, outline="", tags="frame")
        elif state != "recording":
            canvas.create_line(27, 26, 53, 55, fill=self.BACKGROUND, width=5, tags="frame")
            canvas.create_line(27, 26, 53, 55, fill=accent, width=2, tags="frame")
        canvas.create_text(80, 27, text=title, anchor="w", fill="#f8fafc",
                           font=("Segoe UI", 11, "bold"), tags="frame")
        dictation_action = "Finish dictation" if state == "recording" else "Start dictation"
        canvas.create_text(80, 51, text=f"{self._shortcut} · {dictation_action}",
                           anchor="w", fill="#cbd5e1", font=("Segoe UI", 9), tags="frame")
        video_action = {
            "starting": "Cancel screen recording", "recording": "Stop & save video",
            "saving": "Saving video…",
        }.get(video_phase, "Open screen recorder")
        video_color = "#fb7185" if video_phase == "recording" else "#93c5fd"
        canvas.create_rectangle(73, 66, self.WIDTH-35, 92, fill="#1e293b", outline="",
                                tags=("frame", "video_control"))
        canvas.create_text(80, 79, text=f"{self._video_shortcut} · {video_action}",
                           anchor="w", fill=video_color, font=("Segoe UI", 9),
                           tags=("frame", "video_control"))
        # Keep the quit hit target stable while redrawing the animation.
        if not canvas.find_withtag("quit"):
            canvas.create_oval(self.WIDTH-30, 11, self.WIDTH-10, 31, fill=self.BACKGROUND,
                               outline="", tags="quit")
            canvas.create_text(self.WIDTH-20, 20, text="\u00d7", fill="#94a3b8",
                               font=("Segoe UI", 12), tags="quit")
        canvas.tag_raise("quit")

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._root.destroy()
