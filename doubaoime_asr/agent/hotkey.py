from __future__ import annotations

from pynput import keyboard


SPECIAL_KEYS: dict[str, keyboard.Key] = {
    f"f{i}": getattr(keyboard.Key, f"f{i}")
    for i in range(1, 13)
}
SPECIAL_KEYS.update(
    {
        "space": keyboard.Key.space,
        "insert": keyboard.Key.insert,
        "pause": keyboard.Key.pause,
        "scroll_lock": keyboard.Key.scroll_lock,
    }
)


def normalize_hotkey(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def hotkey_matches(key: keyboard.Key | keyboard.KeyCode, configured: str) -> bool:
    normalized = normalize_hotkey(configured)

    if normalized in SPECIAL_KEYS:
        return key == SPECIAL_KEYS[normalized]

    if isinstance(key, keyboard.KeyCode) and key.char:
        return key.char.lower() == normalized

    return False


def hotkey_label(value: str) -> str:
    normalized = normalize_hotkey(value)
    if normalized.startswith("f") and normalized[1:].isdigit():
        return normalized.upper()
    return normalized.replace("_", " ").upper()
