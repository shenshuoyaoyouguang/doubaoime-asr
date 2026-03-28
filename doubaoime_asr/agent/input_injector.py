from __future__ import annotations

import ctypes
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Iterable


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_BACK = 0x08
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_V = 0x56
VK_INSERT = 0x2D
WM_PASTE = 0x0302
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAPVK_VK_TO_VSC = 0

user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SendMessageW.restype = wintypes.LPARAM
user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.c_void_p)
user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.restype = ctypes.c_int
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL


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


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]


@dataclass(frozen=True, slots=True)
class FocusTarget:
    hwnd: int
    focus_hwnd: int | None = None
    process_id: int | None = None
    process_name: str | None = None
    window_class: str | None = None
    focus_class: str | None = None
    is_terminal: bool = False
    terminal_kind: str | None = None


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


def get_window_class_name(hwnd: int) -> str | None:
    if not hwnd:
        return None
    buffer = ctypes.create_unicode_buffer(256)
    size = user32.GetClassNameW(hwnd, buffer, len(buffer))
    if size <= 0:
        return None
    return buffer.value


def get_window_process_id(window_hwnd: int) -> int | None:
    if not window_hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window_hwnd, ctypes.byref(pid))
    return int(pid.value or 0) or None


def get_process_name(pid: int | None) -> str | None:
    if not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(512)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name
        return None
    finally:
        kernel32.CloseHandle(handle)


def classify_focus_target(process_name: str | None, window_class: str | None, focus_class: str | None) -> tuple[bool, str | None]:
    normalized_process = (process_name or "").casefold()
    classes = {(window_class or "").casefold(), (focus_class or "").casefold()}
    if normalized_process == "windowsterminal.exe" or "cascadia_hosting_window_class" in classes:
        return True, "windows_terminal"
    if normalized_process in {"openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe"}:
        return True, "console"
    if "consolewindowclass" in classes:
        return True, "console"
    return False, None


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


def send_ctrl_shift_v() -> None:
    events = [
        _key_input(VK_CONTROL),
        _key_input(VK_SHIFT),
        _key_input(VK_V),
        _key_input(VK_V, KEYEVENTF_KEYUP),
        _key_input(VK_SHIFT, KEYEVENTF_KEYUP),
        _key_input(VK_CONTROL, KEYEVENTF_KEYUP),
    ]
    _send_inputs(events)


def send_shift_insert() -> None:
    events = [
        _key_input(VK_SHIFT),
        _key_input(VK_INSERT),
        _key_input(VK_INSERT, KEYEVENTF_KEYUP),
        _key_input(VK_SHIFT, KEYEVENTF_KEYUP),
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
        focus_hwnd = get_focus_hwnd(hwnd)
        process_id = get_window_process_id(hwnd)
        process_name = get_process_name(process_id)
        window_class = get_window_class_name(hwnd)
        focus_class = get_window_class_name(focus_hwnd or 0)
        is_terminal, terminal_kind = classify_focus_target(process_name, window_class, focus_class)
        return FocusTarget(
            hwnd=hwnd,
            focus_hwnd=focus_hwnd,
            process_id=process_id,
            process_name=process_name,
            window_class=window_class,
            focus_class=focus_class,
            is_terminal=is_terminal,
            terminal_kind=terminal_kind,
        )

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
