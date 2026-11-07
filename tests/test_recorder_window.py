from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from whisperer.app import WhispererApp
from whisperer.config import WhispererConfig


def test_video_hotkey_requests_are_consumed_on_the_ui_thread():
    app = WhispererApp(WhispererConfig(), Mock(), Mock(), Mock(), Mock())
    listener, bubble = Mock(), Mock()
    with patch("whisperer.recorder_window.RecorderWindow") as window:
        app._video_requested.set()
        app._poll_tools(listener, bubble)
        window.assert_called_once_with(bubble.root, "~/Videos/Whisperer", "<ctrl>+p")
        window.return_value.toggle.assert_called_once()
        app._poll_tools(listener, bubble)
        window.return_value.toggle.assert_called_once()  # Event is consumed once.


def test_panel_controls_layout_and_new_recording_without_real_devices(tmp_path):
    import tkinter as tk
    from whisperer.recorder_window import RecorderWindow

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk desktop unavailable")
    root.withdraw()
    screen = Mock()
    area = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    screen.__enter__ = Mock(return_value=Mock(monitors=[area, area]))
    screen.__exit__ = Mock(return_value=False)
    window = None
    try:
        with patch("mss.mss", return_value=screen), patch("whisperer.recorder_window.CameraFeed"):
            window = RecorderWindow(root, str(tmp_path), "<ctrl>+p")
            assert window._webcam.get()
            assert window._microphone.get()
            assert window._clicks.get()
            window._webcam.set(False)
            window.toggle()
            window.poll()
            root.update()
            assert len(window._borders) == 4
            assert all(border.cget("bg") == window.PREVIEW_COLOR for border in window._borders)
            assert "Not recording" in window._status.cget("text")
            assert window._window.winfo_ismapped()
            panel_height = window._window.winfo_height()
            for button in (window._record_button, window._play_button, window._folder_button):
                assert button.winfo_rooty() + button.winfo_height() <= window._window.winfo_rooty() + panel_height
            window.recorder._update(phase="recording")
            window.poll()
            assert str(window._monitor.cget("state")) == "disabled"
            assert "Stop" in window._record_button.cget("text")
            assert all(border.cget("bg") == window.RECORDING_COLOR for border in window._borders)
            window.toggle()
            assert window.recorder._stop.is_set()
            saved = tmp_path / "example.mp4"
            saved.write_bytes(b"test video")
            window.recorder._update(phase="saved", path=saved)
            window.poll()
            root.update()
            assert not window._borders
            assert str(window._play_button.cget("state")) == "normal"
            assert "Saved" in window._status.cget("text")
            window._record_or_stop()  # New recording reopens preview without starting capture.
            assert window.recorder.snapshot.phase == "idle"
            assert len(window._borders) == 4
            assert window._record_button.cget("text") == "●  Record"
            assert all(border.cget("bg") == window.PREVIEW_COLOR for border in window._borders)
            window._dismiss()
            assert not window._visible
            assert not window._borders
    finally:
        if window is not None:
            window.close()
        root.destroy()
