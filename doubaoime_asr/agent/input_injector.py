from __future__ import annotations

import ctypes
from dataclasses import dataclass
from ctypes import wintypes
from typing import Iterable


user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_BACK = 0x08
VK_CONTROL = 0x11
VK_V = 0x56
WM_PASTE = 0x0302

user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SendMessageW.restype = wintypes.LPARAM
user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.c_void_p)
user32.GetGUIThreadInfo.restype = wintypes.BOOL


class FocusChangedError(RuntimeError):
    """当前焦点不再是会话锁定目标。"""


ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]


@dataclass(frozen=True, slots=True)
class FocusTarget:
    hwnd: int
    focus_hwnd: int | None = None


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def utf16_code_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow())


def get_focus_hwnd(window_hwnd: int) -> int | None:
    if not window_hwnd:
        return None
    pid = wintypes.DWORD()
    thread_id = user32.GetWindowThreadProcessId(window_hwnd, ctypes.byref(pid))
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return None
    focus_hwnd = int(info.hwndFocus or 0)
    return focus_hwnd or None


def _send_inputs(inputs: Iterable[INPUT]) -> None:
    buffer = tuple(inputs)
    if not buffer:
        return

    sent = user32.SendInput(
        len(buffer),
        (INPUT * len(buffer))(*buffer),
        ctypes.sizeof(INPUT),
    )
    if sent != len(buffer):
        raise OSError(ctypes.get_last_error(), "SendInput failed")


def _key_input(vk: int, flags: int = 0, scan: int = 0) -> INPUT:
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=vk,
            wScan=scan,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _unicode_inputs(text: str) -> list[INPUT]:
    events: list[INPUT] = []
    for char in text:
        codepoint = ord(char)
        events.append(_key_input(0, KEYEVENTF_UNICODE, codepoint))
        events.append(_key_input(0, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, codepoint))
    return events


def _virtual_key_inputs(vk: int) -> list[INPUT]:
    return [
        _key_input(vk),
        _key_input(vk, KEYEVENTF_KEYUP),
    ]


def send_ctrl_v() -> None:
    events = [
        _key_input(VK_CONTROL),
        _key_input(VK_V),
        _key_input(VK_V, KEYEVENTF_KEYUP),
        _key_input(VK_CONTROL, KEYEVENTF_KEYUP),
    ]
    _send_inputs(events)


def send_wm_paste(hwnd: int) -> None:
    if not hwnd:
        raise OSError("invalid hwnd for WM_PASTE")
    user32.SendMessageW(hwnd, WM_PASTE, 0, 0)


class WindowsTextInjector:
    def capture_target(self) -> FocusTarget | None:
        hwnd = get_foreground_window()
        if not hwnd:
            return None
        return FocusTarget(hwnd=hwnd, focus_hwnd=get_focus_hwnd(hwnd))

    def ensure_target(self, target: FocusTarget) -> None:
        current = get_foreground_window()
        if current != target.hwnd:
            raise FocusChangedError("输入目标已变化")

    def replace_text(
        self,
        target: FocusTarget,
        previous_text: str,
        new_text: str,
    ) -> None:
        self.ensure_target(target)

        events: list[INPUT] = []
        for _ in range(utf16_code_units(previous_text)):
            events.append(_key_input(VK_BACK))
            events.append(_key_input(VK_BACK, KEYEVENTF_KEYUP))

        events.extend(_unicode_inputs(new_text))
        _send_inputs(events)

    def type_text(self, target: FocusTarget, text: str) -> None:
        self.ensure_target(target)
        _send_inputs(_unicode_inputs(text))
