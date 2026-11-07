"""Small Tk recorder panel, screen outline and draggable circular webcam."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from whisperer.screen_recorder import CameraFeed, ScreenRecorder


def exclude_from_capture(window) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    window.update_idletasks()
    native = ctypes.WinDLL("user32", use_last_error=True)
    native.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    native.GetAncestor.restype = wintypes.HWND
    native.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    native.SetWindowDisplayAffinity.restype = wintypes.BOOL
    hwnd = native.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
    # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+). The camera is composited
    # separately, so its desktop preview must not be recorded a second time.
    return bool(native.SetWindowDisplayAffinity(hwnd, 0x11))


def place(window, x: int, y: int, width: int, height: int) -> None:
    window.geometry(f"{width}x{height}+0+0")
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        native = ctypes.WinDLL("user32", use_last_error=True)
        native.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        native.GetAncestor.restype = wintypes.HWND
        native.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        native.SetWindowPos.restype = wintypes.BOOL
        window.update_idletasks()
        hwnd = native.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
        native.SetWindowPos(hwnd, -1, x, y, width, height, 0x0010)  # no activation
    else:
        window.geometry(f"{width}x{height}{x:+d}{y:+d}")


class RecorderWindow:
    PREVIEW_COLOR = "#60a5fa"
    RECORDING_COLOR = "#f43f5e"

    def __init__(self, root, output_dir: str, shortcut: str) -> None:
        from importlib import import_module

        import mss

        # Fail before creating windows or starting devices if an older editable
        # install hasn't installed the new recording dependencies yet.
        for module in ("cv2", "imageio_ffmpeg", "PIL.ImageTk"):
            import_module(module)
        self._root = root
        self._shortcut = shortcut.replace("<", "").replace(">", "").upper()
        self._camera = CameraFeed()
        self.recorder = ScreenRecorder(Path(output_dir), self._camera)
        with mss.mss() as capture:
            self._monitors = [dict(monitor) for monitor in capture.monitors[1:]]
        if not self._monitors:
            raise RuntimeError("No screen is available to record.")
        self._visible = False
        self._last_phase = "idle"
        self._last_error = ""
        self._closed = False
        self._position = (0.10, 0.82)
        self._photo = None
        self._borders = []
        self._window = tk.Toplevel(root)
        self._window.withdraw()
        self._window.title("Whisperer · Screen recorder")
        self._window.resizable(False, False)
        self._window.attributes("-topmost", True)
        self._window.configure(bg="#111827")
        self._window.protocol("WM_DELETE_WINDOW", self._dismiss)
        self._webcam = tk.BooleanVar(value=True)
        self._microphone = tk.BooleanVar(value=True)
        self._clicks = tk.BooleanVar(value=True)

        body = tk.Frame(self._window, bg="#111827", padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Screen recorder", font=("Segoe UI", 17, "bold"),
                 bg="#111827", fg="#f8fafc").pack(anchor="w")
        tk.Label(body, text=f"Click Record to start · {self._shortcut} to stop & save",
                 bg="#111827", fg="#94a3b8", pady=6).pack(anchor="w")
        labels = [f"Screen {i} · {m['width']} × {m['height']}"
                  for i, m in enumerate(self._monitors, 1)]
        self._monitor = ttk.Combobox(body, values=labels, state="readonly", width=43)
        self._monitor.current(0)
        self._monitor.pack(fill="x", pady=(10, 12))
        self._monitor.bind("<<ComboboxSelected>>", lambda _: self._show_outline())
        self._options = []
        for label, variable in (("Webcam bubble", self._webcam),
                                ("Microphone", self._microphone),
                                ("Highlight clicks", self._clicks)):
            button = tk.Checkbutton(
                body, text=label, variable=variable, command=self._options_changed,
                bg="#111827", fg="#e2e8f0", selectcolor="#1e293b",
                activebackground="#111827", activeforeground="white",
                font=("Segoe UI", 10), anchor="w",
            )
            button.pack(fill="x")
            self._options.append(button)
        self._status = tk.Label(body, text="", wraplength=350, justify="left",
                                bg="#111827", fg="#cbd5e1", pady=12, height=6)
        self._status.pack(fill="x")
        self._record_button = tk.Button(
            body, text="●  Record", command=self._record_or_stop, width=26,
            bg="#e11d48", fg="white", activebackground="#be123c",
            activeforeground="white", relief="flat", pady=9,
            font=("Segoe UI", 11, "bold"), cursor="hand2",
        )
        self._record_button.pack(fill="x")
        actions = tk.Frame(body, bg="#111827")
        actions.pack(fill="x", pady=(12, 0))
        self._play_button = ttk.Button(actions, text="Play video", command=self._play, state="disabled")
        self._play_button.pack(side="left")
        self._folder_button = ttk.Button(actions, text="Show in folder", command=self._show_folder, state="disabled")
        self._folder_button.pack(side="right")
        tk.Label(body, text="Compact MP4 · saved on this computer", bg="#111827",
                 fg="#94a3b8", pady=10).pack()

        self._camera_window = tk.Toplevel(root)
        self._camera_window.withdraw()
        self._camera_window.overrideredirect(True)
        self._camera_window.attributes("-topmost", True)
        self._camera_window.configure(bg="#010203")
        if sys.platform == "win32":
            self._camera_window.attributes("-transparentcolor", "#010203")
        self._camera_canvas = tk.Canvas(self._camera_window, bg="#010203",
                                        highlightthickness=0, cursor="fleur")
        self._camera_canvas.pack(fill="both", expand=True)
        self._camera_canvas.bind("<B1-Motion>", self._drag_camera)
        panel_excluded = exclude_from_capture(self._window)
        camera_excluded = exclude_from_capture(self._camera_window)
        self._excluded = panel_excluded and camera_excluded
        exclude_from_capture(root)

    @property
    def _area(self) -> dict:
        return self._monitors[max(0, self._monitor.current())]

    @property
    def _diameter(self) -> int:
        area = self._area
        return min(220, area["width"] // 4, area["height"] // 3)

    def toggle(self) -> None:
        state = self.recorder.snapshot
        if state.phase in ("starting", "recording"):
            self.recorder.stop()
            return
        if self._visible and state.phase != "saving":
            self._dismiss()
            return
        self._visible = True
        self._window.deiconify()
        self._window.lift()
        area = self._area
        place(self._window, area["left"] + area["width"] - 440,
              area["top"] + 50, 410, self._window.winfo_reqheight())
        if state.phase == "idle":
            self._show_outline()
            self._options_changed()

    def _show_outline(self) -> None:
        self._hide_outline()
        if not self._visible:
            return
        area = self._area
        x, y, w, h = (area[key] for key in ("left", "top", "width", "height"))
        for bx, by, bw, bh in ((x, y, w, 3), (x, y+h-3, w, 3),
                                (x, y, 3, h), (x+w-3, y, 3, h)):
            border = tk.Toplevel(self._root)
            border.withdraw()
            border.overrideredirect(True)
            border.attributes("-topmost", True)
            border.configure(bg=self.RECORDING_COLOR if self.recorder.snapshot.phase == "recording"
                             else self.PREVIEW_COLOR)
            place(border, bx, by, bw, bh)
            exclude_from_capture(border)
            border.deiconify()
            self._borders.append(border)
        self._position_camera()

    def _hide_outline(self) -> None:
        for border in self._borders:
            border.destroy()
        self._borders.clear()

    def _options_changed(self) -> None:
        if self._webcam.get() and self._visible:
            self._camera.start()
            # Older/non-Windows capture APIs can include the preview. Hide it
            # during recording if exclusion isn't available to avoid two faces.
            if self._excluded or self.recorder.snapshot.phase not in ("starting", "recording"):
                self._camera_window.deiconify()
                self._position_camera()
        else:
            self._camera.stop()
            self._camera_window.withdraw()

    def _position_camera(self) -> None:
        area, d = self._area, self._diameter
        x = area["left"] + round(self._position[0] * area["width"]) - d // 2
        y = area["top"] + round(self._position[1] * area["height"]) - d // 2
        place(self._camera_window, x, y, d, d)
        self.recorder.set_camera_position(*self._position)

    def _drag_camera(self, event) -> None:
        area, radius = self._area, self._diameter / 2
        x = max(radius, min(area["width"] - radius, event.x_root - area["left"]))
        y = max(radius, min(area["height"] - radius, event.y_root - area["top"]))
        self._position = (x / area["width"], y / area["height"])
        self._position_camera()

    def _record_or_stop(self) -> None:
        state = self.recorder.snapshot
        if state.phase in ("starting", "recording"):
            self.recorder.stop()
        elif state.phase in ("saved", "error"):
            self.recorder.reset()
            self._last_error = ""
            self._options_changed()
            self._show_outline()
            self.poll()
        elif state.phase != "saving":
            if self._webcam.get() and self._camera.frame is None:
                self._status.configure(text=self._camera.error or "Webcam is starting. Try again in a moment.")
                return
            self._last_error = ""
            self._show_outline()
            if not self._excluded:
                self._camera_window.withdraw()
            self.recorder.start(self._area, webcam=self._webcam.get(),
                                microphone=self._microphone.get(), clicks=self._clicks.get())
            self.poll()

    def poll(self) -> None:
        if self._closed:
            return
        state = self.recorder.snapshot
        phase = state.phase
        busy = phase in ("starting", "recording", "saving")
        if phase != self._last_phase:
            self._last_phase = phase
            if phase == "recording":
                for border in self._borders:
                    border.configure(bg=self.RECORDING_COLOR)
            for option in self._options:
                option.configure(state="disabled" if busy else "normal")
            self._monitor.configure(state="disabled" if busy else "readonly")
            self._record_button.configure(
                text={"starting": "■  Stop", "recording": "■  Stop recording", "saving": "Saving…",
                      "saved": "New recording", "error": "Try again"}.get(phase, "●  Record"),
                state="disabled" if phase == "saving" else "normal",
            )
            self._play_button.configure(state="normal" if phase == "saved" else "disabled")
            self._folder_button.configure(state="normal" if state.path is not None else "disabled")
            if phase in ("saving", "saved", "error"):
                self._hide_outline()
                self._camera.stop()
                self._camera_window.withdraw()
            if phase == "saved":
                size = state.path.stat().st_size / 1024**2
                self._status.configure(text=f"Saved · {size:.1f} MB\n{state.path}")
            elif phase == "error":
                self._last_error = state.error
                self._status.configure(text=state.error)
        if phase == "recording":
            seconds = int(state.elapsed)
            self._status.configure(text=f"● Recording  {seconds // 60:02d}:{seconds % 60:02d}\n{self._shortcut} also stops the recording")
        elif phase == "saving":
            self._status.configure(text="Finishing your MP4…")
        elif phase == "starting":
            self._status.configure(text="Starting screen and microphone…")
        elif phase == "idle" and not self._last_error:
            self._status.configure(text=self._camera.error if self._webcam.get() and self._camera.error
                                   else "Not recording. The blue border marks the selected screen.\n"
                                        "Click Record to start; the border will turn red.")
        if self._visible and self._webcam.get() and phase not in ("saving", "saved", "error"):
            self._draw_camera()

    def _draw_camera(self) -> None:
        import cv2
        from PIL import Image, ImageDraw, ImageTk

        d, frame = self._diameter, self._camera.frame
        self._camera_canvas.delete("all")
        if frame is None:
            self._camera_canvas.create_oval(2, 2, d-2, d-2, fill="#111827", outline="#f8fafc", width=3)
            self._camera_canvas.create_text(d/2, d/2, text="Camera unavailable" if self._camera.error else "Starting camera…",
                                            fill="white", width=d-30)
            return
        h, w = frame.shape[:2]
        side = min(h, w)
        frame = frame[(h-side)//2:(h+side)//2, (w-side)//2:(w+side)//2]
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((d, d), Image.Resampling.LANCZOS)
        mask = Image.new("L", (d, d))
        ImageDraw.Draw(mask).ellipse((2, 2, d-2, d-2), fill=255)
        image.putalpha(mask)
        self._photo = ImageTk.PhotoImage(image, master=self._camera_window)
        self._camera_canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._camera_canvas.create_oval(2, 2, d-2, d-2, outline="#f8fafc", width=3)

    def _dismiss(self) -> None:
        if self.recorder.snapshot.phase in ("starting", "recording"):
            self.recorder.stop()
            return
        if self.recorder.snapshot.phase == "saving":
            return
        self._visible = False
        self._camera.stop()
        self._camera_window.withdraw()
        self._window.withdraw()
        self._hide_outline()

    def _play(self) -> None:
        path = self.recorder.snapshot.path
        if path is not None and path.is_file():
            self._open(path)

    def _show_folder(self) -> None:
        path = self.recorder.snapshot.path
        if path is None:
            return
        if sys.platform == "win32" and path.is_file():
            subprocess.Popen(["explorer.exe", "/select,", str(path)], **self._hidden_process())
        else:
            self._open(path if path.is_dir() else path.parent)

    @staticmethod
    def _hidden_process() -> dict:
        return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

    def _open(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except OSError as error:
            self._status.configure(text=f"Could not open file: {error}\n{path}")

    def close(self) -> None:
        self._closed = True
        self.recorder.stop()
        self.recorder.wait()  # Preserve an in-progress video when Whisperer quits.
        self._camera.close()
