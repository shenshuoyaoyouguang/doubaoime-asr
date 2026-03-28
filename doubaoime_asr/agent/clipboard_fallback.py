from __future__ import annotations

import asyncio
import ctypes
from dataclasses import dataclass
import time

import win32clipboard
import win32con


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetClipboardSequenceNumber.restype = ctypes.c_uint


@dataclass(slots=True)
class ClipboardSnapshot:
    text: str | None
    sequence: int


def _open_clipboard_with_retry(retries: int = 10, delay_s: float = 0.02) -> None:
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(delay_s)


def capture_clipboard_text() -> ClipboardSnapshot:
    sequence = int(user32.GetClipboardSequenceNumber())
    _open_clipboard_with_retry()
    try:
        text = None
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return ClipboardSnapshot(text=text, sequence=sequence)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_text(text: str) -> int:
    _open_clipboard_with_retry()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        return int(user32.GetClipboardSequenceNumber())
    finally:
        win32clipboard.CloseClipboard()


async def restore_clipboard_text(
    snapshot: ClipboardSnapshot,
    *,
    expected_sequence: int,
    delay_s: float = 0.12,
) -> bool:
    await asyncio.sleep(delay_s)
    current_sequence = int(user32.GetClipboardSequenceNumber())
    if current_sequence != expected_sequence:
        return False

    _open_clipboard_with_retry()
    try:
        win32clipboard.EmptyClipboard()
        if snapshot.text is not None:
            win32clipboard.SetClipboardText(snapshot.text, win32con.CF_UNICODETEXT)
        return True
    finally:
        win32clipboard.CloseClipboard()
