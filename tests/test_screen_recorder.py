"""Exercise real MP4 encoding using synthetic screen, webcam and microphone data."""

import subprocess
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import cv2
import imageio_ffmpeg
import numpy as np
import pytest

from whisperer.screen_recorder import (
    FPS, HIDDEN_PROCESS, CameraFeed, ScreenRecorder, composite, output_size,
)

AREA = {"left": -640, "top": -100, "width": 640, "height": 360}


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Worker did not reach the expected state")


class SyntheticScreen:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def grab(self, area):
        image = np.zeros((area["height"], area["width"], 4), dtype=np.uint8)
        image[:, :, :3] = (45, 55, 65)
        return image


class SyntheticMicrophone:
    def __init__(self, *, callback, samplerate, **kwargs):
        self.callback, self.samplerate = callback, samplerate
        self.closed = False
        self.stopped = threading.Event()

    def start(self):
        def produce():
            samples = (np.sin(np.arange(480) * 2 * np.pi * 440 / self.samplerate) * 2000)
            while not self.stopped.is_set():
                self.callback(samples.astype(np.int16)[:, None], len(samples), None, False)
                self.stopped.wait(0.01)
        self.worker = threading.Thread(target=produce)
        self.worker.start()

    def stop(self):
        self.stopped.set()
        self.worker.join()

    def close(self):
        self.closed = True


def test_compositing_burns_camera_cursor_and_clicks_at_negative_monitor_coordinates():
    frame = SyntheticScreen().grab(AREA)
    face = np.full((60, 80, 3), (30, 180, 30), dtype=np.uint8)
    image = composite(frame, AREA, face, (0.2, 0.7), (-200, 0),
                      [(10.0, -120, 90), (8.0, -20, 200)], 10.1)
    assert image.shape == (360, 640, 3)
    assert tuple(image[252, 128]) == (30, 180, 30)
    assert image[100:120, 440:458].max() == 255  # cursor
    assert np.any(image[175:205, 505:535, 2] > 200)  # live ripple
    assert tuple(image[300, 620]) == (45, 55, 65)  # expired click
    assert tuple(image[170, 48]) == (45, 55, 65)  # outside camera circle


@pytest.mark.parametrize("size,expected", [
    ((3840, 2160), (1280, 720)), ((1080, 1920), (720, 1280)),
    ((801, 601), (800, 600)), ((640, 360), (640, 360)),
])
def test_output_is_bounded_and_compatible_with_h264(size, expected):
    assert output_size(*size) == expected


@pytest.mark.parametrize("microphone,slow", [(False, False), (True, False), (True, True)])
def test_real_mp4_has_correct_duration_audio_and_camera_and_cleans_intermediates(microphone, slow):
    camera = Mock(frame=np.full((60, 80, 3), (30, 180, 30), dtype=np.uint8))
    screen = SyntheticScreen()
    original_grab = screen.grab
    if slow:
        def slow_grab(area):
            time.sleep(0.13)  # Slower than the requested 20 fps.
            return original_grab(area)
        screen.grab = slow_grab
    streams = []

    def make_stream(**kwargs):
        stream = SyntheticMicrophone(**kwargs)
        streams.append(stream)
        return stream

    # Windows' execution sandbox blocks renaming even newly-created files in
    # its redirected OS temp directory. Exercise the real publish inside the workspace.
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch("mss.mss", return_value=screen), patch(
        "pynput.mouse.Controller", return_value=Mock(position=(-200, 0))
    ), patch("pynput.mouse.Listener") as hook, patch(
        "sounddevice.query_devices", return_value={"default_samplerate": 48000}
    ), patch("sounddevice.InputStream", side_effect=make_stream):
        recorder = ScreenRecorder(Path(directory), camera)
        recorder.set_camera_position(0.2, 0.7)
        recorder.start(AREA, webcam=True, microphone=microphone, clicks=True)
        try:
            wait_for(lambda: recorder.snapshot.phase in ("recording", "error"))
            assert recorder.snapshot.phase == "recording", recorder.snapshot.error
            recorder._on_click(-120, 90, None, True)
            time.sleep(0.38)
            requested_elapsed = recorder.snapshot.elapsed
        finally:
            recorder.stop()
            recorder.wait()
        state = recorder.snapshot
        assert state.phase == "saved", state.error
        assert list(Path(directory).iterdir()) == [state.path]
        assert state.path.stat().st_size < 200_000
        video = cv2.VideoCapture(str(state.path))
        try:
            assert video.isOpened()
            assert video.get(cv2.CAP_PROP_FPS) == FPS
            duration = video.get(cv2.CAP_PROP_FRAME_COUNT) / FPS
            assert abs(duration - state.elapsed) < 1 / FPS
            # Slow capture must preserve wall-clock playback, including on a
            # busy test host; only a final in-progress capture can add a frame.
            assert abs(duration - requested_elapsed) <= 0.15
            ok, image = video.read()
            assert ok
            assert image[252, 128, 1] > 150  # Camera is present in the encoded file.
        finally:
            video.release()
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(state.path),
             "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "48000", "pipe:1"],
            capture_output=True, timeout=10, **HIDDEN_PROCESS,
        )
        if microphone:
            assert result.returncode == 0, result.stderr
            assert abs(len(result.stdout) / 96000 - duration) < 0.08
            assert np.frombuffer(result.stdout, np.int16).max() > 1000
            assert streams[0].closed
        else:
            assert result.returncode != 0  # Screen-only mode must not add microphone audio.
        hook.return_value.stop.assert_called_once()


def test_capture_failure_keeps_recovery_files_and_releases_microphone():
    screen = SyntheticScreen()
    screen.grab = Mock(side_effect=RuntimeError("Screen disconnected"))
    streams = []

    def make_stream(**kwargs):
        stream = SyntheticMicrophone(**kwargs)
        streams.append(stream)
        return stream

    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory, patch("mss.mss", return_value=screen), patch(
        "pynput.mouse.Controller", return_value=Mock(position=(0, 0))
    ), patch("sounddevice.query_devices", return_value={"default_samplerate": 48000}), patch(
        "sounddevice.InputStream", side_effect=make_stream
    ):
        recorder = ScreenRecorder(Path(directory), Mock())
        recorder.start(AREA, webcam=False, microphone=True, clicks=False)
        recorder.wait()
        state = recorder.snapshot
        assert state.phase == "error"
        assert "Screen disconnected" in state.error
        assert "Captured files kept in" in state.error
        assert state.path.is_dir()
        assert (state.path / "microphone.wav").exists()
        assert streams[0].closed
        assert not streams[0].worker.is_alive()


def test_webcam_failure_is_reported_before_opening_any_recording_device(tmp_path):
    recorder = ScreenRecorder(tmp_path, Mock(frame=None, error="Webcam unavailable"))
    with patch("sounddevice.InputStream") as audio, patch("mss.mss") as screen:
        recorder.start(AREA, webcam=True, microphone=True, clicks=True)
        recorder.wait()
    assert recorder.snapshot.phase == "error"
    assert "Webcam unavailable" in recorder.snapshot.error
    audio.assert_not_called()
    screen.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_camera_can_be_turned_off_and_on_and_releases_the_device():
    devices = []

    def open_camera(*args):
        device = Mock()
        device.read.return_value = (True, np.ones((4, 4, 3), dtype=np.uint8))
        devices.append(device)
        return device

    with patch("cv2.VideoCapture", side_effect=open_camera):
        camera = CameraFeed()
        try:
            camera.start()
            wait_for(lambda: camera.frame is not None)
            camera.stop()
            assert camera.frame is None
            wait_for(lambda: devices[0].release.called)
            camera.start()
            wait_for(lambda: camera.frame is not None)
            assert len(devices) == 2
        finally:
            camera.close()
        assert all(device.release.called for device in devices)
