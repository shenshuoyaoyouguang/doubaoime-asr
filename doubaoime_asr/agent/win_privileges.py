from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20
SW_SHOWNORMAL = 1
WORKER_ONLY_FLAGS = {"--worker"}
WORKER_ONLY_VALUE_FLAGS = {"--worker-log-path"}

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
advapi32.GetTokenInformation.restype = wintypes.BOOL
shell32.ShellExecuteW.argtypes = (
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_int,
)
shell32.ShellExecuteW.restype = ctypes.c_void_p


class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


def _close_handle(handle: int | None) -> None:
    if handle:
        kernel32.CloseHandle(handle)


def get_process_elevation(pid: int | None) -> bool | None:
    if not pid:
        return None
    process_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process_handle:
        return None

    token_handle = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(token_handle)):
            return None
        elevation = TOKEN_ELEVATION()
        bytes_returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token_handle,
            TOKEN_ELEVATION_CLASS,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(bytes_returned),
        ):
            return None
        return bool(elevation.TokenIsElevated)
    finally:
        _close_handle(int(token_handle.value or 0))
        _close_handle(int(process_handle or 0))


def is_current_process_elevated() -> bool | None:
    return get_process_elevation(os.getpid())


def filter_relaunch_args(args: Sequence[str]) -> list[str]:
    filtered: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in WORKER_ONLY_FLAGS:
            continue
        if arg in WORKER_ONLY_VALUE_FLAGS:
            skip_next = True
            continue
        if any(arg.startswith(f"{flag}=") for flag in WORKER_ONLY_VALUE_FLAGS):
            continue
        filtered.append(arg)
    return filtered


def build_admin_relaunch_command(
    app_args: Sequence[str],
    *,
    executable: str | None = None,
    frozen: bool | None = None,
    module_name: str = "doubaoime_asr.agent.stable_main",
) -> tuple[str, str]:
    executable_path = Path(executable or sys.executable).resolve()
    normalized_args = filter_relaunch_args(app_args)
    frozen_mode = bool(getattr(sys, "frozen", False) if frozen is None else frozen)
    if frozen_mode:
        return str(executable_path), subprocess.list2cmdline(normalized_args)
    params = ["-m", module_name, *normalized_args]
    return str(executable_path), subprocess.list2cmdline(params)


def _shell_execute_runas(executable: str, params: str, cwd: str | None) -> int:
    result = shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        params or None,
        cwd,
        SW_SHOWNORMAL,
    )
    return int(result)


def restart_as_admin(
    app_args: Sequence[str],
    *,
    executable: str | None = None,
    frozen: bool | None = None,
    cwd: str | None = None,
    module_name: str = "doubaoime_asr.agent.stable_main",
) -> bool:
    executable_path, params = build_admin_relaunch_command(
        app_args,
        executable=executable,
        frozen=frozen,
        module_name=module_name,
    )
    working_directory = str(Path(cwd or os.getcwd()).resolve())
    return _shell_execute_runas(executable_path, params, working_directory) > 32
