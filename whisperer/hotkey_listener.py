"""Global hotkey handling: toggle recording and optional taps while recording."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Mapping
from queue import SimpleQueue

from pynput.keyboard import HotKey, Key, KeyCode, Listener

_MODIFIER_VKS = {
    0xA0: 0x10, 0xA1: 0x10, 0xA2: 0x11,
    0xA3: 0x11, 0xA4: 0x12, 0xA5: 0x12, 0x5C: 0x5B,
}
_MODIFIERS = {Key.ctrl, Key.alt, Key.shift, Key.cmd}
_MODIFIER_CODES = {0x10, 0x11, 0x12, 0x5B}


class _SelectiveListener(Listener):
    """Let pynput dispatch a hotkey before suppressing it in other apps.

    Calling suppress_event() inside win32_event_filter raises before pynput
    queues its own callback. The Windows listener checks this property after
    queuing the callback, so the shortcut both works and stays out of the editor.
    """

    suppress_current_event = False

    @property
    def suppress(self) -> bool:
        return self.suppress_current_event

    def _convert(self, code, msg, lpdata):
        # pynput also checks suppress after conversions that skip the filter
        # (e.g. negative hook codes). Never reuse the last key's decision.
        self.suppress_current_event = False
        return super()._convert(code, msg, lpdata)


def parse_key(key_name: str) -> Key | KeyCode:
    """Turn a config string like 'ctrl_r' or 't' into a pynput key."""
    if len(key_name) == 1:
        return KeyCode.from_char(key_name)
    try:
        return Key[key_name]
    except KeyError as error:
        raise ValueError(f"Unknown key name: {key_name!r}") from error


def keys_match(pressed: Key | KeyCode | None, target: Key | KeyCode) -> bool:
    """True if the event is the target key, including Ctrl+letter variants."""
    if pressed is None:
        return False
    if pressed == target:
        return True
    return _chars_match(pressed, target) or _virtual_keys_match(pressed, target)


def virtual_key_code(key: Key | KeyCode) -> int | None:
    """Windows virtual-key code for a pynput key, if one can be derived."""
    vk = getattr(key, "vk", None)
    if vk is not None:
        return vk
    char = getattr(key, "char", None)
    if char and len(char) == 1 and char.isalpha():
        return ord(char.upper())
    value = getattr(key, "value", None)
    return getattr(value, "vk", None)


def _chars_match(pressed: Key | KeyCode, target: Key | KeyCode) -> bool:
    pressed_char = getattr(pressed, "char", None)
    target_char = getattr(target, "char", None)
    if not pressed_char or not target_char:
        return False
    if pressed_char.lower() == target_char.lower():
        return True
    return _is_ctrl_letter(pressed_char, target_char)


def _is_ctrl_letter(pressed_char: str, target_char: str) -> bool:
    if len(pressed_char) != 1 or len(target_char) != 1 or not target_char.isalpha():
        return False
    return ord(pressed_char) == (ord(target_char.lower()) - ord("a") + 1)


def _virtual_keys_match(pressed: Key | KeyCode, target: Key | KeyCode) -> bool:
    pressed_vk = getattr(pressed, "vk", None)
    if pressed_vk is None:
        return False
    target_vk = virtual_key_code(target)
    return target_vk is not None and pressed_vk == target_vk


class HotkeyListener:
    """Maps raw keyboard events to record-start/stop and while-recording taps."""

    def __init__(
        self,
        record_key: str,
        on_record_start: Callable[[], None],
        on_record_stop: Callable[[], None],
        recording_taps: Mapping[Key | KeyCode, Callable[[], None]] | None = None,
    ) -> None:
        record_keys = (
            HotKey.parse(record_key) if "+" in record_key else [parse_key(record_key)]
        )
        self._record_keys = {self._normalize_key(key) for key in record_keys}
        self._record_hotkey = HotKey(
            self._record_keys, self._toggle_recording
        )
        self._record_vks = {
            vk for key in record_keys
            if (vk := virtual_key_code(self._normalize_key(key))) is not None
        }
        self._pressed_vks: set[int] = set()
        self._consumed_vks: set[int] = set()
        self._on_record_start = on_record_start
        self._on_record_stop = on_record_stop
        self._recording_taps = dict(recording_taps or {})
        self._recording = False
        self._listener: Listener | None = None
        self._listener_joined = False
        self._callbacks: SimpleQueue[Callable[[], None] | None] | None = None
        self._callback_worker: threading.Thread | None = None
        self._callback_error: Exception | None = None
        self._stopping = threading.Event()
        self._suppressed_vks = {
            vk
            for key in self._recording_taps
            if (vk := virtual_key_code(key)) is not None
        }

    def run(self) -> None:
        """Block forever, dispatching hotkey events. Ctrl+C in the console exits."""
        self.start()
        try:
            self._join_listener()
            self._raise_callback_error()
        finally:
            self.stop()

    def start(self) -> None:
        """Start hotkeys without blocking the main-thread indicator loop."""
        if self._callback_worker is not None:
            raise RuntimeError("Hotkeys are already started.")
        self._stopping.clear()
        self._callback_error = None
        self._listener_joined = False
        self._pressed_vks.clear()
        self._consumed_vks.clear()
        self._recording = False
        for key in self._record_keys:
            self._record_hotkey.release(key)
        listener_kwargs: dict = {
            "on_press": self._on_press,
            "on_release": self._on_release,
        }
        if sys.platform == "win32":
            listener_kwargs["win32_event_filter"] = self._win32_event_filter
        listener_factory = _SelectiveListener if sys.platform == "win32" else Listener
        self._listener = listener_factory(**listener_kwargs)
        self._callbacks = SimpleQueue()
        self._callback_worker = threading.Thread(
            target=self._run_callbacks, name="whisperer-hotkey-actions", daemon=True,
        )
        self._callback_worker.start()
        try:
            self._listener.start()
            self._listener.wait()
        except Exception:
            self.stop()
            raise

    def check(self) -> None:
        """Surface callback failures to the UI instead of leaving a stale bubble."""
        self._raise_callback_error()
        if self._listener is not None and not self._listener.is_alive():
            self._join_listener()  # re-raises exceptions raised by a callback
            raise RuntimeError("The global hotkey listener stopped.")

    def _join_listener(self) -> None:
        # pynput 1.7's join consumes a one-shot error/stop queue. Calling it
        # again without a timeout can block forever, even on a dead thread.
        if self._listener is not None and not self._listener_joined:
            try:
                self._listener.join()
            except Exception:
                self._listener_joined = True
                raise
            else:
                self._listener_joined = True

    def stop(self) -> None:
        self._stopping.set()
        try:
            if self._listener is not None:
                self._listener.stop()
                self._join_listener()
        finally:
            if self._callback_worker is not None:
                self._callbacks.put(None)
                self._callback_worker.join()
                self._callback_worker = None
                self._callbacks = None

    def _dispatch(self, callback: Callable[[], None]) -> None:
        if self._stopping.is_set():
            return
        if self._callbacks is None:
            callback()  # Also supports direct event dispatch without start().
        else:
            self._callbacks.put(callback)

    def _run_callbacks(self) -> None:
        # Audio devices, logging and recording cleanup can block. Keep all of
        # them off Windows' hook/message-loop thread, in the original order.
        while True:
            callback = self._callbacks.get()
            if callback is None:
                return
            if self._stopping.is_set():
                continue
            try:
                callback()
            except Exception as error:
                self._callback_error = error
                self._stopping.set()
                self._listener.stop()
                return

    def _raise_callback_error(self) -> None:
        if self._callback_error is not None:
            raise RuntimeError("A hotkey action failed; keyboard hook stopped.") from self._callback_error

    def _on_press(self, key: Key | KeyCode | None) -> None:
        if key is None:
            return
        self._record_hotkey.press(self._normalize_key(key))
        if self._recording:
            for tap_key, callback in self._recording_taps.items():
                if keys_match(key, tap_key):
                    self._dispatch(callback)
                    return

    def _on_release(self, key: Key | KeyCode | None) -> None:
        if key is not None:
            normalized = self._normalize_key(key)
            self._record_hotkey.release(normalized)
            if normalized in _MODIFIERS and normalized in self._record_keys:
                # Completing a chord by releasing its modifier must not leave
                # a stale M behind if Windows dropped/delayed that key's up.
                # The next chord needs a fresh letter press, not Ctrl alone.
                for record_key in self._record_keys - _MODIFIERS:
                    self._record_hotkey.release(record_key)

    def _toggle_recording(self) -> None:
        if self._recording:
            self._recording = False
            self._dispatch(self._on_record_stop)
        else:
            self._recording = True
            try:
                self._dispatch(self._on_record_start)
            except Exception:
                self._recording = False
                raise

    @staticmethod
    def _normalize_key(key: Key | KeyCode) -> Key | KeyCode:
        for modifier, variants in (
            (Key.ctrl, (Key.ctrl, Key.ctrl_l, Key.ctrl_r)),
            (Key.alt, (Key.alt, Key.alt_l, Key.alt_r)),
            (Key.shift, (Key.shift, Key.shift_l, Key.shift_r)),
            (Key.cmd, (Key.cmd, Key.cmd_l, Key.cmd_r)),
        ):
            if key in variants:
                return modifier
        if isinstance(key, KeyCode):
            # Windows can report Ctrl+M as '\r' (or with no character).
            # The virtual key still identifies the physical letter key.
            if key.vk is not None and 65 <= key.vk <= 90:
                return KeyCode.from_char(chr(key.vk).lower())
            if key.char is not None:
                return KeyCode.from_char(key.char.lower())
        if isinstance(key, Key):
            return KeyCode.from_vk(key.value.vk)
        return key

    def _win32_event_filter(self, _msg, data) -> bool:
        # Return False skips pynput's callbacks, while leaving the input free to
        # reach the target application. Never let our simulated Ctrl+V (or a
        # clipboard utility's injected keys) change physical hotkey state.
        flags = getattr(data, "flags", 0)
        if isinstance(flags, int) and flags & 0x10:  # LLKHF_INJECTED
            if self._listener is not None:
                self._listener.suppress_current_event = False
            return False
        vk = data.vkCode
        # Windows reports left/right modifier VKs; HotKey.parse uses generic VKs.
        canonical_vk = _MODIFIER_VKS.get(vk, vk)
        suppress = vk in self._consumed_vks
        if _msg in (0x0100, 0x0104):  # WM_KEYDOWN / WM_SYSKEYDOWN
            repeated = vk in self._pressed_vks
            self._pressed_vks.add(vk)
            pressed = {
                _MODIFIER_VKS.get(key, key)
                for key in self._pressed_vks
            }
            # Pressing M before Ctrl must not consume Ctrl: the user may keep
            # holding that same Ctrl for Ctrl+V (or any other application key).
            # A shortcut configured as a single modifier still consumes it.
            modifier_in_chord = canonical_vk in _MODIFIER_CODES and len(self._record_vks) > 1
            shortcut = (
                not modifier_in_chord
                and canonical_vk in self._record_vks
                and self._record_vks <= pressed
            )
            tap = self._recording and vk in self._suppressed_vks
            if not repeated and (shortcut or tap):
                self._consumed_vks.add(vk)
                suppress = True
        elif _msg in (0x0101, 0x0105):  # WM_KEYUP / WM_SYSKEYUP
            self._pressed_vks.discard(vk)
            self._consumed_vks.discard(vk)
        # Pair every release with its initial down, even if recording changed
        # while the key was held. Blocking only Shift-up leaves Windows seeing
        # a stuck modifier, which makes the paste release check time out.
        if self._listener is not None:
            self._listener.suppress_current_event = suppress
        return True
