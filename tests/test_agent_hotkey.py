import pytest

from doubaoime_asr.agent.win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display


def test_normalize_hotkey():
    assert normalize_hotkey("F8") == "f8"
    assert normalize_hotkey("scroll lock") == "scroll_lock"


def test_vk_from_hotkey():
    assert vk_from_hotkey("f8") == 0x77
    assert vk_from_hotkey("space") == 0x20
    assert vk_from_hotkey("a") == ord("A")
    assert vk_from_hotkey("1") == ord("1")


def test_vk_from_hotkey_rejects_unsupported():
    with pytest.raises(ValueError):
        vk_from_hotkey("ctrl+space")


def test_vk_to_display_formats_known_keys():
    assert vk_to_display(0x77) == "F8"
    assert vk_to_display(ord("A")) == "A"
