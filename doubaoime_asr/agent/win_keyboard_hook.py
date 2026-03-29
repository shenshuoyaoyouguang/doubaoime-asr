from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
from typing import Callable

from .win_hotkey import vk_from_hotkey, vk_to_display


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
HC_ACTION = 0
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
MODIFIER_VKS = {
    VK_SHIFT,
    VK_CONTROL,
    VK_MENU,
    VK_LSHIFT,
    VK_RSHIFT,
    VK_LCONTROL,
    VK_RCONTROL,
    VK_LMENU,
    VK_RMENU,
    VK_LWIN,
    VK_RWIN,
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


LPMSG = ctypes.POINTER(wintypes.MSG)
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_longlong
user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = (LPMSG,)
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = (LPMSG,)
user32.DispatchMessageW.restype = wintypes.LPARAM
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class GlobalHotkeyHook:
    def __init__(
        self,
        hotkey: str | int,
        *,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self._vk = hotkey if isinstance(hotkey, int) else vk_from_hotkey(hotkey)
        self._on_press = on_press
        self._on_release = on_release
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook = None
        self._callback = None
        self._started = threading.Event()
        self._pressed = False
        self._error: Exception | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="doubao-hotkey-hook", daemon=True)
        self._thread.start()
        self._started.wait(timeout=2)
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def callback(nCode, wParam, lParam):
            if nCode == HC_ACTION:
                data = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if data.vkCode == self._vk:
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        if not self._pressed:
                            self._pressed = True
                            self._on_press()
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                        if self._pressed:
                            self._pressed = False
                            self._on_release()
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._callback = callback
        module = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback, module, 0)
        if not self._hook:
            self._error = OSError(ctypes.get_last_error(), "SetWindowsHookExW failed")
            self._started.set()
            return

        self._started.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None


class SingleKeyRecorder:
    def __init__(
        self,
        on_key: Callable[[int, str], None],
        *,
        allowed_modifier_vks: set[int] | None = None,
    ) -> None:
        self._on_key = on_key
        self._allowed_modifier_vks = set(allowed_modifier_vks or ())
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook = None
        self._callback = None
        self._started = threading.Event()
        self._error: Exception | None = None
        self._captured = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="doubao-hotkey-recorder", daemon=True)
        self._thread.start()
        self._started.wait(timeout=2)
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()

        @HOOKPROC
        def callback(nCode, wParam, lParam):
            if nCode == HC_ACTION and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                data = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(data.vkCode)
                if self._captured:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)
                if vk in MODIFIER_VKS and vk not in self._allowed_modifier_vks:
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)
                self._captured = True
                self._on_key(vk, vk_to_display(vk))
                if self._thread_id is not None:
                    user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._callback = callback
        module = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback, module, 0)
        if not self._hook:
            self._error = OSError(ctypes.get_last_error(), "SetWindowsHookExW failed")
            self._started.set()
            return

        self._started.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
