"""Checked, tagged Windows paste input, sent as one ordered batch."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

INPUT_TAG = 0x57535052  # WSPR, visible as KBDLLHOOKSTRUCT.dwExtraInfo


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputValue(ctypes.Union):
    # The mouse member is required for INPUT's correct size/alignment on x64.
    _fields_ = [("ki", _KeyboardInput), ("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", _InputValue)]


def _key(vk: int, *, up: bool = False) -> _Input:
    return _Input(1, _InputValue(ki=_KeyboardInput(
        wVk=vk, dwFlags=0x0002 if up else 0, dwExtraInfo=INPUT_TAG,
    )))


def send_paste_shortcut() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    send_input = user32.SendInput
    send_input.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    send_input.restype = wintypes.UINT
    inputs = (_Input * 4)(_key(0x11), _key(0x56), _key(0x56, up=True), _key(0x11, up=True))
    ctypes.set_last_error(0)
    sent = send_input(4, inputs, ctypes.sizeof(_Input))
    if sent != 4:
        error_code = ctypes.get_last_error()
        cleanup_detail = ""
        if sent:
            # A partially inserted batch must not leave Ctrl or V held down.
            releases = (_Input * 2)(_key(0x56, up=True), _key(0x11, up=True))
            released = send_input(2, releases, ctypes.sizeof(_Input))
            if released != 2:
                cleanup_detail = (
                    f" Key release failed ({released}/2 events); tap and release V and Ctrl."
                )
        raise RuntimeError(
            f"Windows accepted {sent}/4 paste key events (error {error_code}). "
            f"Transcript kept on clipboard; use Ctrl+V in the target app.{cleanup_detail}"
        )
