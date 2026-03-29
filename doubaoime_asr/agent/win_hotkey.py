from __future__ import annotations

import ctypes
from ctypes import wintypes


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetKeyNameTextW.argtypes = (wintypes.LONG, wintypes.LPWSTR, ctypes.c_int)
user32.GetKeyNameTextW.restype = ctypes.c_int


FUNCTION_HOTKEYS = [f"f{i}" for i in range(1, 13)]
RIGHT_CTRL_HOTKEY = "right_ctrl"
SPECIAL_HOTKEYS = ["space", "insert", "pause", "scroll_lock", RIGHT_CTRL_HOTKEY]
LETTER_HOTKEYS = list("abcdefghijklmnopqrstuvwxyz")
DIGIT_HOTKEYS = list("0123456789")
SUPPORTED_HOTKEYS = [*FUNCTION_HOTKEYS, *SPECIAL_HOTKEYS, *LETTER_HOTKEYS, *DIGIT_HOTKEYS]

HOTKEY_TO_VK: dict[str, int] = {
    **{name: 0x6F + index for index, name in enumerate(FUNCTION_HOTKEYS, start=1)},
    "space": 0x20,
    "insert": 0x2D,
    "pause": 0x13,
    "scroll_lock": 0x91,
    RIGHT_CTRL_HOTKEY: 0xA3,
}

for char in LETTER_HOTKEYS:
    HOTKEY_TO_VK[char] = ord(char.upper())

for char in DIGIT_HOTKEYS:
    HOTKEY_TO_VK[char] = ord(char)


VK_TO_HOTKEY: dict[int, str] = {value: key for key, value in HOTKEY_TO_VK.items()}
VK_TO_DISPLAY: dict[int, str] = {
    **{HOTKEY_TO_VK[name]: name.upper() for name in FUNCTION_HOTKEYS},
    HOTKEY_TO_VK["space"]: "SPACE",
    HOTKEY_TO_VK["insert"]: "INSERT",
    HOTKEY_TO_VK["pause"]: "PAUSE",
    HOTKEY_TO_VK["scroll_lock"]: "SCROLL LOCK",
    HOTKEY_TO_VK[RIGHT_CTRL_HOTKEY]: "RIGHT CTRL",
}


def normalize_hotkey(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def vk_from_hotkey(value: str) -> int:
    normalized = normalize_hotkey(value)
    if normalized not in HOTKEY_TO_VK:
        raise ValueError(f"不支持的热键: {value}")
    return HOTKEY_TO_VK[normalized]


def vk_to_hotkey(vk: int) -> str | None:
    return VK_TO_HOTKEY.get(vk)


def vk_to_display(vk: int) -> str:
    if vk in VK_TO_DISPLAY:
        return VK_TO_DISPLAY[vk]
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk)

    scan_code = user32.MapVirtualKeyW(vk, 0)
    if scan_code:
        buffer = ctypes.create_unicode_buffer(64)
        result = user32.GetKeyNameTextW(scan_code << 16, buffer, len(buffer))
        if result > 0:
            return buffer.value.upper()
    return f"VK_{vk}"


def hotkey_pressed(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)
