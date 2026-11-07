"""Local screen recording: bounded-memory capture, compositing and MP4 encoding.

Devices and FFmpeg run on workers. Tk only reads snapshots and camera frames.
The intermediate video is already compressed; finishing only muxes the audio.
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

FPS = 20
MAX_EDGE = 1280
HIDDEN_PROCESS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def output_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, MAX_EDGE / max(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def encoder_command(executable: str, size: tuple[int, int], target: Path) -> list[str]:
    width, height = size
    return [
        executable, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
        "-r", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "28", "-maxrate", "1800k",
        "-bufsize", "3600k", "-pix_fmt", "yuv420p",
        "-movflags", "+frag_keyframe+empty_moov", str(target),
    ]


def mux_command(executable: str, video: Path, audio: Path | None,
                duration: float, target: Path) -> list[str]:
    command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
    if audio is not None:
        command += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                    "-c:a", "aac", "-b:a", "64k", "-af", "apad"]
    return command + ["-c:v", "copy", "-t", f"{duration:.6f}",
                      "-movflags", "+faststart", str(target)]


class CameraFeed:
    """Keep only the newest webcam frame, shared by preview and encoder."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._error = ""
        self._enabled = threading.Event()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._enabled.is_set():
            return
        with self._lock:
            self._frame, self._error = None, ""
        self._enabled.set()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="whisperer-webcam", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        import cv2

        while not self._shutdown.is_set():
            if not self._enabled.wait(0.1):
                continue
            camera = None
            try:
                camera = cv2.VideoCapture(0, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(0)
                if not camera.isOpened():
                    raise RuntimeError("Webcam unavailable. Close other camera apps or turn Webcam off.")
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                while self._enabled.is_set() and not self._shutdown.is_set():
                    ok, frame = camera.read()
                    if not ok:
                        raise RuntimeError("Webcam disconnected. Turn Webcam off to record the screen alone.")
                    with self._lock:
                        self._frame = cv2.flip(frame, 1)
                    self._shutdown.wait(1 / 30)
            except Exception as error:
                with self._lock:
                    self._error = str(error)
                self._enabled.clear()
            finally:
                if camera is not None:
                    camera.release()
                with self._lock:
                    self._frame = None

    @property
    def frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None or not self._enabled.is_set() else self._frame.copy()

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    def stop(self) -> None:
        self._enabled.clear()

    def close(self) -> None:
        self.stop()
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def composite(frame: np.ndarray, area: dict, camera: np.ndarray | None,
              position: tuple[float, float], cursor: tuple[int, int] | None,
              clicks: list[tuple[float, int, int]], now: float) -> np.ndarray:
    """Burn the cursor, click ripples and circular camera into the actual file."""
    import cv2

    width, height = output_size(area["width"], area["height"])
    image = cv2.resize(frame[:, :, :3], (width, height), interpolation=cv2.INTER_AREA)
    sx, sy = width / area["width"], height / area["height"]
    for stamp, x, y in clicks:
        age = now - stamp
        if 0 <= age < 0.5:
            point = (round((x - area["left"]) * sx), round((y - area["top"]) * sy))
            cv2.circle(image, point, round(8 + age * 55), (60, 200, 255), 3, cv2.LINE_AA)
    if cursor is not None:
        x = round((cursor[0] - area["left"]) * sx)
        y = round((cursor[1] - area["top"]) * sy)
        if 0 <= x < width and 0 <= y < height:
            points = np.array([(x, y), (x + 4, y + 18), (x + 8, y + 12), (x + 15, y + 12)])
            cv2.fillPoly(image, [points], (255, 255, 255))
            cv2.polylines(image, [points], True, (30, 30, 30), 1, cv2.LINE_AA)
    if camera is not None:
        diameter = min(round(220 * sx), width // 4, height // 3)
        if diameter >= 8:
            radius = diameter // 2
            x = max(0, min(width - diameter, round(position[0] * width) - radius))
            y = max(0, min(height - diameter, round(position[1] * height) - radius))
            h, w = camera.shape[:2]
            side = min(h, w)
            crop = camera[(h-side)//2:(h+side)//2, (w-side)//2:(w+side)//2]
            face = cv2.resize(crop, (diameter, diameter), interpolation=cv2.INTER_AREA)
            mask = np.zeros((diameter, diameter), dtype=np.uint8)
            cv2.circle(mask, (radius, radius), radius - 2, 255, -1, cv2.LINE_AA)
            alpha = mask[:, :, None].astype(np.float32) / 255
            region = image[y:y+diameter, x:x+diameter]
            region[:] = (face * alpha + region * (1 - alpha)).astype(np.uint8)
            cv2.circle(image, (x+radius, y+radius), radius-2, (240, 240, 240), 2, cv2.LINE_AA)
    return np.ascontiguousarray(image)


@dataclass(frozen=True)
class RecordingSnapshot:
    phase: str = "idle"
    elapsed: float = 0.0
    path: Path | None = None
    error: str = ""


class ScreenRecorder:
    def __init__(self, output_dir: Path, camera: CameraFeed) -> None:
        self.output_dir = output_dir.expanduser().resolve()
        self.camera = camera
        self._lock = threading.Lock()
        self._snapshot = RecordingSnapshot()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._stopped: float | None = None
        self._camera_position = (0.10, 0.82)
        self._clicks: deque[tuple[float, int, int]] = deque(maxlen=30)

    @property
    def snapshot(self) -> RecordingSnapshot:
        with self._lock:
            state = self._snapshot
            if state.phase == "recording":
                return replace(state, elapsed=max(0, (self._stopped or time.monotonic()) - self._started))
            return state

    def _update(self, **changes) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)

    def set_camera_position(self, x: float, y: float) -> None:
        with self._lock:
            self._camera_position = (x, y)

    def start(self, area: dict, *, webcam: bool, microphone: bool, clicks: bool) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._snapshot = RecordingSnapshot(phase="starting")
            self._stopped = None
            self._clicks.clear()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, args=(dict(area), webcam, microphone, clicks),
                name="whisperer-screen-recording", daemon=False,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._stopped is None:
                self._stopped = time.monotonic()
            self._stop.set()

    def wait(self) -> None:
        if self._thread is not None:
            self._thread.join()

    def reset(self) -> None:
        with self._lock:
            if self._snapshot.phase in ("saved", "error") and (self._thread is None or not self._thread.is_alive()):
                self._snapshot = RecordingSnapshot()

    def _on_click(self, x, y, _button, pressed) -> None:
        if pressed:
            with self._lock:
                self._clicks.append((time.monotonic(), x, y))

    def _run(self, area: dict, webcam: bool, microphone: bool, clicks: bool) -> None:
        process = stream = audio_file = mouse_hook = None
        work = None
        errors = None
        audio_error: list[str] = []
        try:
            import imageio_ffmpeg
            import mss
            import sounddevice as sd
            import soundfile as sf
            from pynput import mouse

            if webcam and self.camera.frame is None:
                raise RuntimeError(self.camera.error or "Webcam is starting. Try Record again in a moment.")
            executable = imageio_ffmpeg.get_ffmpeg_exe()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            target = self.output_dir / f"Whisperer-{datetime.now():%Y-%m-%d_%H-%M-%S-%f}.mp4"
            work = Path(tempfile.mkdtemp(prefix=".recording-", dir=self.output_dir))
            video, audio = work / "screen.mp4", work / "microphone.wav"
            errors = (work / "encoder.log").open("w+b")
            process = subprocess.Popen(
                encoder_command(executable, output_size(area["width"], area["height"]), video),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, **HIDDEN_PROCESS,
            )
            if microphone:
                sample_rate = int(sd.query_devices(kind="input")["default_samplerate"])
                audio_file = sf.SoundFile(audio, mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16")

                def on_audio(indata, _frames, _timing, status):
                    if status:
                        audio_error[:] = [f"Microphone capture failed: {status}"]
                        self.stop()
                    try:
                        audio_file.write(indata)
                    except Exception as error:
                        audio_error[:] = [f"Microphone write failed: {error}"]
                        self.stop()

                stream = sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16", callback=on_audio)
            controller = mouse.Controller()
            if clicks:
                mouse_hook = mouse.Listener(on_click=self._on_click)
                mouse_hook.start()
            with mss.mss() as capture:
                if stream is not None:
                    stream.start()
                with self._lock:
                    self._started = time.monotonic()
                self._update(phase="recording")
                frames = 0
                previous = None
                while not self._stop.is_set():
                    now = time.monotonic()
                    # A slow capture repeats the previous frame rather than
                    # speeding the file up and drifting away from microphone audio.
                    due = int((now - self._started) * FPS)
                    if previous is not None:
                        while frames < due and not self._stop.is_set():
                            self._write_frame(process, previous)
                            frames += 1
                    with self._lock:
                        position, ripples = self._camera_position, list(self._clicks)
                    camera = self.camera.frame if webcam else None
                    if webcam and camera is None:
                        raise RuntimeError(self.camera.error or "Webcam stopped sending frames.")
                    previous = composite(np.asarray(capture.grab(area)), area, camera,
                                         position, controller.position, ripples, now)
                    self._write_frame(process, previous)
                    frames += 1
                    self._stop.wait(max(0, self._started + frames / FPS - time.monotonic()))
                elapsed = max(0, (self._stopped or time.monotonic()) - self._started)
                # Include the final partial frame; stopping during startup still
                # produces one valid frame instead of an empty MP4.
                if previous is None:
                    previous = composite(np.asarray(capture.grab(area)), area,
                                         self.camera.frame if webcam else None,
                                         self._camera_position, None, [], time.monotonic())
                while frames < max(1, math.ceil(elapsed * FPS)):
                    self._write_frame(process, previous)
                    frames += 1
            self._update(phase="saving", elapsed=frames / FPS)
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()
                    stream = None
            if audio_file is not None:
                audio_file.close()
                audio_file = None
            process.stdin.close()
            process.wait(timeout=30)
            if process.returncode:
                raise RuntimeError("Video encoder failed; see encoder.log in the recovery folder.")
            if audio_error:
                raise RuntimeError(audio_error[0])
            # Write beside the intermediates so an interrupted mux cannot leave
            # a file that looks like a finished recording in the user's folder.
            finished = work / "finished.mp4"
            result = subprocess.run(
                mux_command(executable, video, audio if microphone else None, frames / FPS, finished),
                capture_output=True, timeout=60, **HIDDEN_PROCESS,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace")[-1500:])
            finished.replace(target)
            errors.close()
            errors = None
            for file in (video, audio, work / "encoder.log"):
                with suppress(OSError):
                    file.unlink(missing_ok=True)
            with suppress(OSError):
                work.rmdir()
            self._update(phase="saved", path=target)
        except Exception as error:
            recovery = f"\nCaptured files kept in: {work}" if work is not None else ""
            self._update(phase="error", error=f"{error}{recovery}", path=work)
        finally:
            if mouse_hook is not None:
                with suppress(Exception):
                    mouse_hook.stop()
                    mouse_hook.join(timeout=2)
            if stream is not None:
                with suppress(Exception):
                    stream.stop()
                with suppress(Exception):
                    stream.close()
            if audio_file is not None:
                with suppress(Exception):
                    audio_file.close()
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                if process.poll() is None:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if errors is not None:
                errors.close()

    @staticmethod
    def _write_frame(process, frame: np.ndarray) -> None:
        # FileIO.write may return a short write, especially for a large frame.
        remaining = memoryview(frame).cast("B")
        while remaining:
            written = process.stdin.write(remaining)
            if not written:
                raise RuntimeError("Video encoder closed unexpectedly.")
            remaining = remaining[written:]
