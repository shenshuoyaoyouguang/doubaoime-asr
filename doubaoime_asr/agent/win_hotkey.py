from __future__ import annotations

import ctypes


user32 = ctypes.WinDLL("user32", use_last_error=True)


HOTKEY_TO_VK: dict[str, int] = {
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    "space": 0x20,
    "insert": 0x2D,
    "pause": 0x13,
    "scroll_lock": 0x91,
}

for char in "abcdefghijklmnopqrstuvwxyz":
    HOTKEY_TO_VK[char] = ord(char.upper())

for char in "0123456789":
    HOTKEY_TO_VK[char] = ord(char)


def normalize_hotkey(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def vk_from_hotkey(value: str) -> int:
    normalized = normalize_hotkey(value)
    if normalized not in HOTKEY_TO_VK:
        raise ValueError(f"不支持的热键: {value}")
    return HOTKEY_TO_VK[normalized]


def hotkey_pressed(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)
